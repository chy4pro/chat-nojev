"""候选回复 + 判断：一次调用干完两件事。

上游是两个模型两次调用：判断层（本地 decider-2b 或云端 TypeSafe Jev）出意图和风险、
再单独问一次「哪条回复最合适」；生成层（这里）只写候选。这一版把判断题原文铺进生成
提示词（题目在 src/questions.py，一个字没改），让写候选的模型顺手把意图、风险、具体行动
和每条候选的分一起吐出来——**产出方换了，形状没换**：hud 拿到的还是原来那个 verdict
dict（intent / confidence / intent_probs / risk / risk_probs / actions / message / backend）。

下面 `_verdict()` 那段适配只做翻译：把模型给的值搬到 hud 认识的键下面，值本身原样递下去。
不查意图表、不夹风险范围、不归一概率——上游的判断模型一样会吐垃圾，所以校验放在用的
地方（hud.applyJudgment_ 拿到不是数的风险显示「风险待判断」、拿到不在表里的意图就照着
显示模型自己的说法）。在这一层再校验一遍，只会让同一份坏值在我们这儿的表现跟上游不一样。
模型整条没给的字段就不出现，绝不替它编一个值。

话术仍然是**一种话术一次调用、并发发出去**（上游的做法，理由见 Generator.generate），
所以判断跟着每一次调用一起回来，按槽位顺序取第一份读得出来的——详见 generate() 的说明。

Two API shapes are supported, because providers disagree:
    openai     POST {base}/v1/chat/completions   Authorization: Bearer   -> choices[0].message.content
    anthropic  POST {base}/v1/messages           x-api-key + version    -> content[].text
推 most providers (DeepSeek, 通义, Moonshot, SiliconFlow, Ollama, vLLM, OpenRouter) only
speak the OpenAI shape; 智谱 and a few gateways offer both. User key prefixes select
the shape; built-in credentials infer it from the base URL.

Nothing is ever written back, and the key is never logged. Run
`uv run python src/generate.py --check` to see which source is in use (key masked).

Privacy: the boss's message text is sent to the provider. That is the one place this app
leaves the machine — swap in a local model if that matters more than reply quality.
"""

from __future__ import annotations

import concurrent.futures
import http.client
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from pathlib import Path

import builtin
import questions
import userconfig
import styles

DEFAULT_MODEL = "glm-4-flash"
# any Anthropic-compatible /v1/messages endpoint works; this one is a cheap, fast
# Chinese-native option and is what the project was tested against
DEFAULT_BASE = "https://open.bigmodel.cn/api/anthropic"
DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
DEFAULT_ANTHROPIC_BASE = "https://api.anthropic.com"
MISSING_HINT = ("未配置 Key：候选回复和意图/风险判断都要它（同一次调用）。"
                "设置 OPENAI_API_KEY（或 ANTHROPIC_API_KEY）后重启，见 README 配置章节。")
# 上游一次只要 {n} 行纯文本，300 够了。判断跟着一起回来之后，光 judgment 带两张概率表
# （意图 8 项 + 风险 10 档）就装不下 300，截断了整个对象都废——所以抬到这一档。
# 仍然是「写几句回复」的量级，不是思考型模型的预算。
#
# 上游 v0.6.0 起每话术的候选数可配（styles.PER_TONE，1–5），而候选和判断在同一个 JSON 里，
# 一截断就是整个对象作废、这一路话术全空。所以预算跟着候选数走：判断那两张表是固定开销,
# 每多一条候选再加一份「一句回复 + 一个分」的量。PER_TONE=2（默认）仍然是 1200。
_JUDGMENT_BUDGET = 900          # 两张概率表 + intent/risk/confidence/actions
_PER_CANDIDATE_BUDGET = 150     # 一条回复 + 它在 reply_scores 里那一项


def max_tokens(per_tone: int) -> int:
    """这一路话术这次调用的输出预算。每次调用时读 styles.PER_TONE,设置页改了就跟着变。"""
    return _JUDGMENT_BUDGET + _PER_CANDIDATE_BUDGET * max(1, int(per_tone))


MAX_TOKENS = max_tokens(2)      # 默认 PER_TONE 下的值,只给日志和测试看
# 用的是随包分发的凭据时报这个来源名，日志/--check 里能一眼分清「内置」和「你自己配的」
BUILTIN_SOURCE = "内置默认"


class _KeepAlivePool:
    """std 库 keep-alive 连接池：按 (scheme, host, port) 复用 http.client 连接。

    urllib.urlopen 每次请求都新建 DNS+TCP+TLS（一条连接 ~0.1–0.3 s 白付掉），而
    生成层每条消息至少发一次、两个话术并发发两次，换话术再发两次。这里空闲连接
    表有锁；一条连接同一时刻只属于一个请求，所以并发调用天然各拿各的连接。

    从池里取出的连接可能是服务端已悄悄关掉的（keep-alive 超时），因此网络类异常
    换新连接重试一次——与 urllib3 的做法一致。HTTP >= 300 不重试，按调用方依赖的
    urllib.error.HTTPError 形状抛出（e.read() 仍能拿到错误正文）。不跟随重定向：
    LLM 端点不会 30x，真遇到就以 HTTPError 形式可见，而不是静默 GET 掉。
    """

    def __init__(self, max_idle: int = 4):
        self._lock = threading.Lock()
        self._idle: dict[tuple, list] = {}
        self._max_idle = max_idle

    def _checkout(self, scheme, host, port, timeout):
        key = (scheme, host, port)
        with self._lock:
            idle = self._idle.get(key)
            if idle:
                return key, idle.pop()
        cls = (http.client.HTTPSConnection if scheme == "https"
               else http.client.HTTPConnection)
        return key, cls(host, port, timeout=timeout)

    def _checkin(self, key, conn):
        with self._lock:
            idle = self._idle.setdefault(key, [])
            if len(idle) < self._max_idle:
                idle.append(conn)
                return
        conn.close()

    def post_json(self, url: str, headers: dict, body: dict, timeout: float) -> dict:
        p = urllib.parse.urlparse(url)
        scheme = p.scheme or "https"
        port = p.port or (443 if scheme == "https" else 80)
        path = p.path + (("?" + p.query) if p.query else "")
        payload = json.dumps(body).encode()
        last_exc: Exception | None = None
        for _attempt in range(2):
            key, conn = self._checkout(scheme, p.hostname, port, timeout)
            try:
                conn.request("POST", path, body=payload, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            except (http.client.HTTPException, OSError) as e:
                conn.close()
                last_exc = e
                continue
            if resp.will_close:
                conn.close()
            else:
                self._checkin(key, conn)
            if resp.status >= 300:
                raise urllib.error.HTTPError(
                    url, resp.status, resp.reason, resp.headers, io.BytesIO(data))
            return json.loads(data)
        assert last_exc is not None
        raise last_exc


_POOL = _KeepAlivePool()


def http_post_json(url: str, headers: dict, body: dict, timeout: float) -> dict:
    """模块级 POST 入口：这一版全程只有生成层在用它（上游还有 judge_jev 共用）。

    设置页的「测试连接」也走它（src/settings_config.py）。"""
    return _POOL.post_json(url, headers, body, timeout)


class ThinkingOnlyError(Exception):
    """A reasoning model spent the whole max_tokens budget thinking and wrote no text.

    DeepSeek-style reasoning models return the chain of thought alongside the answer; with
    this app's per-request budget (MAX_TOKENS) the thinking can consume everything
    and `content` arrives empty. That is a wrong-model problem, not a network one, so the
    error names the model and the fix — the panel would otherwise fold it into 「空结果」,
    which reads as "generation is broken" instead of "the model is misconfigured".
    """


# The message carried by ThinkingOnlyError. Fits the panel's err[:60] display budget for
# realistic model names (fixed part is 41 chars), so the suggestion survives truncation.
# {alt} is a non-thinking model the configured endpoint actually serves (see _call).
THINKING_ONLY_HINT = ("思考型 {model}：额度被思考耗尽，正文 0 条；"
                      "换非思考模型（如 {alt}）")

# One request per tone. {n} appears twice on purpose: the "exactly n lines" demand has to
# agree with the count asked for, or the model pads the answer with a line of its own.
#
# The boldness line is what gives a tone its edges. Without it both replies sit at the same
# safe distance and every tone reads a bit flat; with it the first is always something you
# could send as-is and the second is where the persona gets to breathe. Measured on the
# built-in tones: 卑微乙方's pair goes from two polite apologies to "收到收到…" plus
# "您息怒我马上跪着改完给您磕头了", and 贴吧老哥 picks up "我自己看了都想删号".
#
# 上游这里只要 {n} 行纯文本。判断合进来之后改成一个 JSON 对象，字段一一对应 hud 要的
# 那几样：候选、每条的分、判断那三项。**replies 必须放第一个**——流式那一路是从没写完的
# JSON 里抠已经收口的候选字符串（_replies_segment），judgment 先来的话候选就只能等整条回完。
PROMPT_ONE = """刚收到一条聊天消息，你要帮我回。

{context_line}消息：「{message}」
{intent_line}
请写 {n} 条回复候选，语气统一成下面这一种：
「{tone}」{instruction}

硬性要求：
- {variation}
- 每条不超过 30 个字，是聊天软件里打字的语气，不要客套话、不要解释
- 不要写出语气名称（不要写「{tone}：」这类前缀），直接从回复内容开始

输出：只输出一个 JSON 对象，别的什么都别写——不要 Markdown 围栏，不要解释，不要前言后语。
字段按下面这个顺序给，"replies" 必须是第一个：
- "replies"：恰好 {n} 个字符串的数组，就是上面说的那 {n} 条回复本身，不要编号、不要引号包两层；
- "reply_scores"：{n} 个 0~1 的数的数组，跟 replies 一一对应；
- "judgment"：对象，按下面的说明给 intent / confidence / intent_probs / risk / risk_probs / actions。
  哪一条你判断不出来就把那一条整个省掉，不要填 null、不要瞎猜。
所有字符串用双引号，不要有尾逗号，不要写注释。

判断说明（judgment 照这里答；说的是这条收到的消息，不是你写的回复）：

{judgment_spec}"""


def _variation_instruction(count: int) -> str:
    if count == 1:
        return "只写一条稳妥、可以直接发出去的回复"
    return "前一条稳妥、可以直接发出去；最后一条把这个语气做足，更皮、更夸张一点也行"


# The model is told not to label its lines, and usually complies — but "usually" is exactly
# why these exist. Seen for real: "轻松型：" (the intended echo), "轻松的回复：", "轻松版：",
# "轻松一点：", "**轻松型**：", and behind numbering ("1. 轻松型：" — the numbering is
# stripped first, leaving the bare label). So: a style word, then up to a few characters of
# filler that may not contain sentence punctuation, then the colon.
_STYLE_LABEL = re.compile(
    r"^[*_#\s]*(稳妥|轻松|简短|简洁)[^，。！？；、,.!?;：:]{0,5}[*_#\s]*[:：]\s*")
# One-character style words match only when the colon follows almost immediately: a loose
# filler here would eat a legitimate reply like "简单说：我先确认一下".
_STYLE_LABEL_SHORT = re.compile(r"^[*_#\s]*(简|稳|轻)\s*(型|洁)?[*_#\s]*[:：]\s*")
# wrapping quotes, straight or CJK — applied before the label strip, and again after it,
# because either order can expose the other ("「轻松型：xxx」")
_QUOTES = re.compile(r"""^["“”「『'‘]+|["”」』'’]+$""")


def _strip_style_label(s: str) -> str:
    s = _STYLE_LABEL.sub("", s)
    s = _STYLE_LABEL_SHORT.sub("", s)
    return styles.strip_label(s)


def _strip_quotes(s: str) -> str:
    return _QUOTES.sub("", s)


# ---------------------------------------------------------------------------
# 合并那一次调用的读法：对象 → 候选 / 每条的分 / 判断。
# 只干两件事——把 JSON 从回答里抠出来（解析），把值搬到 hud 认识的键下面（翻译）。
# 不做校验：值合不合法由 hud 判断，它本来就得防着上游那个判断模型吐垃圾。
# ---------------------------------------------------------------------------

# 只匹配已经收口的 JSON 字符串。流式解析靠它：还没写完那条压根匹配不上，
# 所以不会把半句话当成一条候选推上屏。
_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def _json_object(content: str) -> dict:
    """从模型输出里抠出那个 JSON 对象。围栏、前后废话都兜住；截断了就返回空 dict。"""
    content = re.sub(r"^```(?:json)?|```$", "", (content or "").strip(),
                     flags=re.MULTILINE).strip()
    for text in (content, content[content.find("{"):content.rfind("}") + 1]):
        if not text:
            continue
        try:
            obj = json.loads(text)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def _replies_segment(content: str) -> list[str]:
    """截断（或还在流）的回答里已经写完的那几条候选。

    `{"replies": ["甲","乙` → ['甲', '乙']；半截那条被正则跳过。
    整条 JSON 写完之后这个函数和 json.loads 得到的是同一批字符串，所以流式提前推上屏
    的行和最后那次权威解析对得上（上游同样的纪律：流只是早看一眼，parse 才算数）。
    """
    m = re.search(r'"replies"\s*:\s*\[(.*?)(?:\]|$)', content or "", re.S)
    if not m:
        return []
    out = []
    for s in _JSON_STRING.finditer(m.group(1)):
        try:
            out.append(str(json.loads(s.group(0))))
        except ValueError:
            pass
    return out


def _clean_reply(s: str) -> str:
    """一条候选的清洗，跟上游逐行解析那一套一模一样（编号、引号、语气名前缀）。"""
    s = re.sub(r"^[\d]+[.、)．]\s*", "", (s or "").strip())
    s = _strip_quotes(s)
    s = _strip_style_label(s)
    return _strip_quotes(s).strip()


def _scores_by_text(obj: dict, texts: list[str]) -> dict:
    """reply_scores → {候选原文: 分}。模型的三种写法都认，值原样递下去。

    数组（跟 replies 一一对应）、{候选原文: 分}、{"1": 分} 这种一基下标——认写法是解析，
    不是校验；分本身不动：不归一、不夹范围、不转类型。上游 rank_candidates 会 float() 一道，
    这里不做，坏值由用的地方兜（hud._rank_payload 排序、_render_groups 显示都防着）。
    """
    raw = obj.get("reply_scores")
    if isinstance(raw, (list, tuple)):
        # 数组是按模型自己写的 replies 顺序排的，而 texts 是清洗后**丢掉空条目**的结果。
        # 直接跟 texts 对齐的话，空条目后面每一条都会拿到前一条的分。所以先按模型原样的
        # 顺序配对，再挑出还留着的那几条。模型没给 replies 数组（截断、或退回逐行读）时
        # 没有原序可依，只能按现有顺序对齐。
        original = obj.get("replies")
        if isinstance(original, (list, tuple)):
            paired: dict = {}
            for text, value in zip((_clean_reply(str(o)) for o in original), raw):
                if text and text not in paired:
                    paired[text] = value
            return {t: paired[t] for t in texts if t in paired}
        return {t: v for t, v in zip(texts, raw)}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for i, text in enumerate(texts):
        for key in (text, str(i + 1), str(i)):
            if key in raw:
                out[text] = raw[key]
                break
    return out


def _verdict(obj: dict, message: str, model: str) -> dict | None:
    """judgment 那一坨 → hud 认识的 verdict dict。没读出任何一项就返回 None。

    搬键，不动值：意图不查 questions.INTENTS、风险不夹 0..9、概率表不归一、actions 原样收。
    模型没给的字段就不出现（上游判断模型没答这题也是这个效果），绝不替它编一个——
    只有 message 和 backend 是我们自己填的，那两个本来就不是模型的输出。

    confidence / intent_probs / risk_probs 是**模型自己报的自评**，不是上游判断模型那种
    校准过的概率：同一个模型写了候选又给自己打分，数值只能当它的语气看，别当概率用。
    """
    raw = obj.get("judgment")
    if not isinstance(raw, dict):
        raw = obj          # 有的模型不套 judgment，把几个字段直接摊在顶层
    out = {}
    for name in ("intent", "confidence", "intent_probs", "risk", "risk_probs", "actions"):
        if raw.get(name) is not None:
            out[name] = raw[name]
    if not out:
        return None        # 判断整块没读出来：hud 把这次当判断失败，候选照常出
    out["message"] = message
    out["backend"] = model
    return out


_VERSION_SEG = re.compile(r"v\d+[a-z]*")


def _base_segments(base: str) -> list[str]:
    """Path segments of a base URL, empty pieces stripped (`…/v1/` -> ['v1'])."""
    return [s for s in urllib.parse.urlsplit((base or "").rstrip("/")).path.split("/")
            if s]


def base_has_version_segment(base: str) -> bool:
    """True when the base URL already ends in a version segment (`…/v1`, `…/v4`).

    Providers disagree about whether the version belongs to the base, so both spellings
    must compose to the same request URL.

    上游这条规则还被判断层的 `jev_request_url()` 共用（#42：TYPESAFE_BASE_URL 也得
    认三种写法）。判断合进这一次调用之后判断层没了，那个函数和
    `base_is_verbatim_action()` 一起删掉了——现在只有生成层的端点在用它。
    """
    segs = _base_segments(base)
    return bool(segs) and bool(_VERSION_SEG.fullmatch(segs[-1].lower()))


def _endpoint(base: str, api: str) -> str:
    """Compose the request URL, tolerating both base-URL conventions.

    Providers disagree about whether the version segment belongs to the base:
        https://api.deepseek.com             -> /v1/chat/completions
        https://api.deepseek.com/v1          -> /v1/chat/completions
        https://open.bigmodel.cn/api/paas/v4 -> /v4/chat/completions
    So: if the base already ends in a version segment, append only the path.
    """
    b = (base or "").rstrip("/")
    has_version = base_has_version_segment(b)
    if api == "anthropic":
        return b + ("/messages" if has_version else "/v1/messages")
    return b + ("/chat/completions" if has_version else "/v1/chat/completions")


def pick_api_format(base: str, configured: str | None) -> str:
    """Explicit setting wins; otherwise infer from the URL.

    A base path containing "anthropic" means the Anthropic shape. Every other endpoint is
    assumed OpenAI-shaped, which is what most providers and local servers expose.
    """
    if configured:
        c = configured.strip().lower()
        if c in ("openai", "anthropic"):
            return c
    return "anthropic" if "anthropic" in (base or "").lower() else "openai"


_BUILTIN_MODEL: str | None = None


def _resolve_builtin_model(base: str) -> str:
    """Pick a model name the relay actually serves — asked once per process.

    A distributed bundle freezes whatever name is compiled into it, so the day the relay
    behind it gains or loses a channel every copy in the wild would start failing with
    "no permission for this model". Asking the relay what it offers keeps those copies
    working across channel swaps. Any failure falls back to builtin.MODEL: resolution is
    an optimisation, never a precondition.
    """
    global _BUILTIN_MODEL
    if _BUILTIN_MODEL is not None:
        return _BUILTIN_MODEL
    offered: list[str] = []
    try:
        req = urllib.request.Request(f"{base.rstrip('/')}/models",
                                     headers={"authorization": f"Bearer {builtin.API_KEY}"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            offered = [m.get("id") for m in (json.load(resp).get("data") or []) if m.get("id")]
    except Exception:
        pass          # offline / not an OpenAI-shaped relay: fall through to the pinned name
    for want in (builtin.MODEL, *builtin.MODEL_PREFERENCE):
        if want in offered:
            _BUILTIN_MODEL = want
            return want
    _BUILTIN_MODEL = offered[0] if offered else builtin.MODEL
    return _BUILTIN_MODEL


def load_credentials() -> tuple[str, str, str, str, str]:
    """Returns (base_url, api_key, model, source, api_format). Never raises.

    Names are the conventional ones (src/userconfig.py), so whatever you already export
    for other tools works here:
        OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL         the common case
        ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / ANTHROPIC_MODEL
    User credentials select the API shape by their prefix, including custom endpoints
    whose URL contains no provider name. Built-in credentials still infer from the URL.
    """
    oai = userconfig.provider("OPENAI")
    anth = userconfig.provider("ANTHROPIC")

    if oai["key"]:
        base = oai["base"] or DEFAULT_OPENAI_BASE
        return base, oai["key"], oai["model"] or DEFAULT_MODEL, oai["source"], "openai"
    if anth["key"]:
        base = anth["base"] or DEFAULT_ANTHROPIC_BASE
        return base, anth["key"], anth["model"] or DEFAULT_MODEL, anth["source"], "anthropic"

    # 两个都没配：回退到随包分发的内置凭据，让应用开箱就能出候选。位置在最后，
    # 所以内置永远不会盖掉用户显式配的那一组。
    if builtin.API_KEY:
        return (builtin.BASE_URL, builtin.API_KEY, _resolve_builtin_model(builtin.BASE_URL),
                BUILTIN_SOURCE, pick_api_format(builtin.BASE_URL, None))

    base = oai["base"] or anth["base"] or DEFAULT_OPENAI_BASE
    model = oai["model"] or anth["model"] or DEFAULT_MODEL
    return base, "", model, "none", pick_api_format(base, None)


def _extra_params() -> dict:
    """Extra request-body fields from OPENAI_EXTRA_BODY (a JSON object).

    Some endpoints need a switch the OpenAI shape has no field for: an unswitched Qwen3
    spends its whole reply budget reasoning (85s per call vs 2s on the same relay). Field
    names are provider-specific, so this passes through whatever is configured rather than
    naming one option. Malformed JSON is ignored — this runs on every generation, and a
    typo in an optional knob must not be able to take the candidates down.
    """
    raw = userconfig.get("OPENAI_EXTRA_BODY") or builtin.EXTRA_BODY
    if not raw:
        return {}
    try:
        extra = json.loads(raw)
    except ValueError:
        return {}
    return extra if isinstance(extra, dict) else {}


def credential_status() -> str:
    """Human-readable state for --check; the key itself is never printed."""
    base, key, model, source, api = load_credentials()
    shape = ("Anthropic 格式 /v1/messages" if api == "anthropic"
             else "OpenAI 格式 /v1/chat/completions")
    home = str(Path.home())
    if not key:
        return (f"❌ 未配置 API Key\n"
                f"   端点: {base}  ({shape})\n"
                f"   模型: {model}\n"
                f"   {MISSING_HINT}")
    return (f"✅ 凭据来源: {source.replace(home, '~')}\n"
            f"   端点: {base}\n"
            f"   接口: {shape}\n"
            f"   模型: {model}\n"
            f"   Key : {key[:6]}…{key[-4:]}  ({len(key)} chars)")


class Generator:
    def __init__(self, model: str | None = None, timeout: int = 30,
                 api: str | None = None):
        self.model_override = model
        self.api_override = api if api in ("openai", "anthropic") else None
        self.timeout = timeout
        self._creds: tuple[str, str, str] | None = None
        self._last_url = ""

    def _creds_or_load(self):
        if self._creds is None:
            base, key, model, _src, _api = load_credentials()
            self._creds = (base, key, self.model_override or model)
        return self._creds

    def _call(self, prompt: str, on_delta=None) -> str:
        """One completion. With `on_delta`, streams: each content fragment is passed to it
        as it arrives, and the full text is still returned at the end (so the caller can
        parse lines once, authoritatively, from the same string).

        Streaming is OpenAI-shape only (`stream: true` + SSE) — that is what the DeepSeek /
        SiliconFlow / vLLM tier speaks and where the latency win is. The Anthropic shape
        keeps its one-shot request: `on_delta` is silently ignored there. If a gateway
        accepts `stream: true` but answers with plain JSON anyway, the response is parsed
        the old way — streaming degrades, it does not fail.
        """
        base, key, model, _src, api = load_credentials()
        # the constructor's overrides win — without this the `model` argument was accepted
        # and silently ignored, so the request went out with whatever the config named
        if self.model_override:
            model = self.model_override
        if self.api_override:
            api = self.api_override
        # the "switch to this" example should be a model the configured endpoint actually
        # serves: deepseek-chat on OpenAI-shaped providers, this app's non-thinking default
        # (DEFAULT_MODEL) behind the Anthropic shape
        alt = "glm-4-flash" if api == "anthropic" else "deepseek-chat"
        if api == "anthropic":
            url = _endpoint(base, "anthropic")
            body = {"model": model, "max_tokens": max_tokens(styles.PER_TONE), "temperature": 0.9,
                    "messages": [{"role": "user", "content": prompt}]}
            headers = {"content-type": "application/json", "x-api-key": key,
                       "anthropic-version": "2023-06-01"}
            data = self._post(url, headers, body)
            parts = data.get("content") or []
            raw = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            if not raw.strip():
                # extended thinking returns its blocks next to the text blocks; text
                # missing while thinking is present means the budget died mid-thought
                thinking = "".join(p.get("thinking", "") for p in parts
                                   if isinstance(p, dict))
                if thinking.strip():
                    raise ThinkingOnlyError(THINKING_ONLY_HINT.format(model=model, alt=alt))
            return raw

        url = _endpoint(base, "openai")
        body = {"model": model, "max_tokens": max_tokens(styles.PER_TONE), "temperature": 0.9,
                "messages": [{"role": "user", "content": prompt}]}
        body.update(_extra_params())
        headers = {"content-type": "application/json", "authorization": f"Bearer {key}"}
        if on_delta is not None:
            return self._stream_openai(url, headers, body, model, alt, on_delta)
        data = self._post(url, headers, body)
        return self._openai_json(data, model, alt)

    @staticmethod
    def _openai_json(data: dict, model: str, alt: str) -> str:
        """Parse a one-shot OpenAI-shape response; raises on the thinking-only case."""
        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        content = msg.get("content") or ""
        if not content.strip():
            # reasoning lives in reasoning_content (DeepSeek, SiliconFlow) or reasoning
            # (OpenRouter); a string there with empty content is the same wrong-model case
            for field in ("reasoning_content", "reasoning"):
                v = msg.get(field)
                if isinstance(v, str) and v.strip():
                    raise ThinkingOnlyError(THINKING_ONLY_HINT.format(model=model, alt=alt))
        return content

    def _stream_openai(self, url: str, headers: dict, body: dict,
                       model: str, alt: str, on_delta) -> str:
        """SSE variant of the OpenAI-shape call; returns the full content text.

        The wire format is `data: {json}` lines ended by `data: [DONE]`, each carrying a
        `delta` with the next fragment. Reasoning models send their thinking through the
        same deltas (reasoning_content / reasoning) before any content, so a stream that
        ends with thinking and no text is the same wrong-model case as the one-shot path
        and raises the same error — nothing was shown yet, because nothing was emitted.
        """
        self._last_url = url
        req = urllib.request.Request(
            url, data=json.dumps({**body, "stream": True}).encode(), headers=headers)
        content: list[str] = []
        reasoning: list[str] = []
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            ctype = (r.headers.get("content-type") or "").lower()
            if "event-stream" not in ctype:
                # the gateway took `stream: true` but answered with one JSON document:
                # parse it the ordinary way instead of failing
                return self._openai_json(json.load(r), model, alt)
            for raw_line in r:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue        # blank separators, "event:" lines, ": keep-alive"
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    evt = json.loads(payload)
                except ValueError:
                    continue        # a malformed keepalive must not kill the stream
                for ch in evt.get("choices") or []:
                    delta = ch.get("delta") or {}
                    frag = delta.get("content") or ""
                    if frag:
                        content.append(frag)
                        on_delta(frag)
                    for field in ("reasoning_content", "reasoning"):
                        v = delta.get(field)
                        if isinstance(v, str) and v:
                            reasoning.append(v)
        raw = "".join(content)
        if not raw.strip() and "".join(reasoning).strip():
            raise ThinkingOnlyError(THINKING_ONLY_HINT.format(model=model, alt=alt))
        return raw

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        self._last_url = url
        return http_post_json(url, headers, body, self.timeout)

    @staticmethod
    def _parse(raw: str) -> list[str]:
        """逐行读候选——模型没按 JSON 给时的兜底，上游唯一的读法。

        每行的清洗（编号、引号、语气名前缀）跟 JSON 那一路共用 _clean_reply，
        所以两条路读出来的候选长得一模一样。
        """
        return [s for s in (_clean_reply(line) for line in raw.splitlines()) if s]

    @staticmethod
    def _replies(obj: dict, raw: str) -> list[str]:
        """候选：先看对象里的 replies；对象没解析出来就抢 replies 那个数组（截断的回答）；
        再不行退回上游的逐行读法。"""
        got = obj.get("replies")
        if isinstance(got, (list, tuple)):
            texts = [t for t in (_clean_reply(str(x)) for x in got) if t]
            if texts:
                return texts
        texts = [t for t in (_clean_reply(x) for x in _replies_segment(raw)) if t]
        return texts or Generator._parse(raw)

    def _one_tone(self, message: str, intent: str, tone: str,
                  context: str | None = None,
                  on_line=None) -> dict:
        """One request for one tone. Returns a group dict; never raises.

        {"texts": [...], "scores": {原文: 分}, "verdict": dict|None, "error": str}

        With `on_line`, each finished line is handed over the moment it completes so the
        panel can show it before the request ends — the final `texts` stay the one
        authoritative parse of the whole reply, and the callback is only the early look.

        上游这里只回 (texts, error)：判断和排序是另一个模型另外两次调用。这一版同一次
        调用还带回判断和每条候选的分，所以回一个 dict——取值的地方（generate）照样按
        槽位顺序读，形状之外什么都没变。
        """
        # The recent turns go in with their speakers ("王总: …"), because a reply that fits
        # the last two sentences is usually not a reply to this one sentence in isolation.
        context_line = f"最近的对话：\n{context}\n\n" if context else ""
        # 判断现在跟候选是同一次调用的产物，所以线上调用点一律不传 intent（hud 传的就是 ""）；
        # 参数留着是给 __main__ 手测用的，也留着上游那行提示词的位置。
        intent_line = f"判断出的意图：{intent}\n" if intent else ""
        prompt = PROMPT_ONE.format(message=message, context_line=context_line,
                                   intent_line=intent_line,
                                   n=styles.PER_TONE, tone=tone,
                                   instruction=styles.PRESETS[tone],
                                   variation=_variation_instruction(styles.PER_TONE),
                                   judgment_spec=questions.render_judgment_spec())
        fail = {"texts": [], "scores": {}, "verdict": None}
        emitted = 0              # 已经推上屏的行数（= 最终 texts 里的下标）
        consumed = 0             # 已经从流里读掉的候选条数（清洗后为空的也算读过）
        buf = ""                 # 到目前为止收到的全部文本（JSON 得整段前缀一起看）

        def on_delta(frag: str) -> None:
            nonlocal buf, emitted, consumed
            buf += frag
            done = _replies_segment(buf)     # 只有已经收口的字符串会出现在这里
            while consumed < len(done) and emitted < styles.PER_TONE:
                text = _clean_reply(done[consumed])
                consumed += 1
                if text:
                    emitted += 1
                    on_line(text)

        try:
            raw = self._call(prompt, on_delta if on_line is not None else None)
        except ThinkingOnlyError as e:
            return {**fail, "error": "模型仅返回思考内容，请关闭思考模式或更换模型"}
        except urllib.error.HTTPError as e:
            return {**fail, "error": f"HTTP {e.code}：请检查模型服务设置"}
        except Exception as e:
            return {**fail, "error": type(e).__name__}
        obj = _json_object(raw)
        texts = self._replies(obj, raw)[:styles.PER_TONE]
        if on_line is not None:
            # Sync the callback with the authoritative parse. Two ways lines can be
            # missing from what the stream emitted: the model stops without closing the
            # object (the last reply string may still have landed), and a gateway that
            # fell back to one-shot JSON streams nothing at all. Either way the remaining
            # lines go out here, so the panel shows them at this request's end.
            for text in texts[emitted:styles.PER_TONE]:
                emitted += 1
                on_line(text)
        _base, _key, model = self._creds_or_load()
        return {"texts": texts, "scores": _scores_by_text(obj, texts),
                "verdict": _verdict(obj, message, model), "error": ""}

    def generate(self, message: str, intent: str = "",
                 slot_tones: list[str] | None = None,
                 context: str | None = None,
                 on_candidate=None) -> dict:
        """One concurrent request per selected 话术; returns the candidates grouped by tone,
        the score of every candidate, and the judgment.

        A tone gets its own request rather than one request listing every tone: asking a
        single call for "2 in this voice and 2 in that voice" makes the voices bleed into
        each other, and it makes the response harder to split back into groups. Three
        requests in flight together cost about as long as the slowest one.

        判断怎么跟着并发跑：**每一次调用都答一遍判断题，按槽位顺序取第一份读得出来的**。
        上游的判断是完全独立的一次调用，一个话术请求挂了也不影响判断；要保住这一条，
        判断就不能只挂在某一个指定的话术上（那个话术超时、被限流、或者模型只吐了思考，
        判断就跟着没了，而别的话术明明成功了）。代价是多花 N-1 份判断的输出 token——
        两道题带两张概率表，按 flash 档位的价钱基本可以忽略。几份判断不一致时不做合并、
        不投票（那是在编一个模型没给过的值），就按槽位顺序取第一份，结果可复现。
        收敛成一次调用（三种话术写在同一个提示词里）也能省掉这笔，但上游特意不那么干：
        语气会互相串味，候选质量下降——「候选一个字都不能变」比省几个 token 重要。

        `slot_tones` is the panel's per-slot selection (styles.NONE_LABEL marks an unused
        slot). Two slots holding the same tone is allowed and simply runs it twice.

        `on_candidate(slot, tone, text)` fires from the worker threads the moment a line
        completes — streaming's early look, before the full result is in. Callers that do
        not pass it get exactly the old collect-then-return behaviour.
        """
        slots = list(slot_tones or (styles.DEFAULT_SLOTS + [styles.NONE_LABEL]))
        active = [(i, t) for i, t in enumerate(slots) if t in styles.PRESETS]
        if not active:
            return {"groups": [], "verdict": None, "scores": {},
                    "error": "没有选择任何话术", "elapsed_s": 0.0}
        if not self._creds_or_load()[1]:
            return {"groups": [], "verdict": None, "scores": {},
                    "error": MISSING_HINT, "elapsed_s": 0.0}

        t0 = time.perf_counter()
        groups: list[dict] = []

        def run(i: int, tone: str):
            # slot index rides along so the panel knows where the line belongs
            def on_line(text: str) -> None:
                on_candidate(i, tone, text)
            return self._one_tone(message, intent, tone, context,
                                  on_line if on_candidate is not None else None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(active)) as ex:
            futures = {i: ex.submit(run, i, tone) for i, tone in active}
            for i, tone in active:          # read in slot order, not completion order
                try:
                    got = futures[i].result()
                except Exception as e:      # defensive: _one_tone swallows its own errors
                    got = {"texts": [], "scores": {}, "verdict": None,
                           "error": f"{type(e).__name__}: {e}"}
                groups.append({"slot": i, "tone": tone, **got})

        _base, _key, model = self._creds_or_load()
        # 判断：按槽位顺序取第一份读得出来的（见上面的说明）。一份都没有 = 判断这次没出来，
        # hud 把它当判断失败处理，候选照常上屏——跟上游判断调用挂了是同一个效果。
        verdict = next((g["verdict"] for g in groups if g.get("verdict")), None)
        # 每条候选的分，合成一张 {原文: 分} 的表，hud._rank_payload 按原文查。
        # 注意口径变了：上游是把所有话术的候选放在一道题里让判断模型一次排完，分是跨话术可比的；
        # 这一版每次调用只看得见自己写的那两条，分是**话术内**的。面板拿它做的事没变
        # （组内排序 + 显示百分比），组内的先后一个字不差，跨组那个百分比不再是同一把尺子。
        scores: dict = {}
        for g in groups:
            scores.update(g.get("scores") or {})
        # when nothing came back from any tone, the per-group reasons are the only
        # diagnosis there is — lift them to the top level so the panel shows e.g.
        # "思考型 deepseek-v4-pro：…" instead of hud's generic 「空结果」 fallback
        error = ""
        if not any(g["texts"] for g in groups):
            seen: list[str] = []
            for g in groups:
                e = (g.get("error") or "").strip()
                if e and e not in seen:      # same wrong model -> same hint N times
                    seen.append(e)
            error = " · ".join(seen)
        return {"groups": groups, "model": model, "verdict": verdict, "scores": scores,
                "error": error, "elapsed_s": time.perf_counter() - t0}


if __name__ == "__main__":
    import sys

    if "--check" in sys.argv:
        print(credential_status())
        raise SystemExit(0 if load_credentials()[1] else 1)

    g = Generator()
    msg = sys.argv[1] if len(sys.argv) > 1 else "这个需求你今天跟一下"
    intent = sys.argv[2] if len(sys.argv) > 2 else "派活"
    print(json.dumps(g.generate(msg, intent), ensure_ascii=False, indent=1))
