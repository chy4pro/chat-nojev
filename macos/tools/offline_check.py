# -*- coding: utf-8 -*-
"""不联网的合并调用自测：把一串「模型可能吐出来的东西」喂给解析器，看面板还能不能用。

    python tools/offline_check.py

只替换 src/generate.py 里那个发 HTTP 的函数（http_post_json，唯一出网的地方），
其余整条链都是真的：真的 Generator.generate（含并发、每种话术一次调用）、真的解析器、
真的适配层，以及 **src/hud.py 里真正那几行渲染代码**——面板那几个读法是用 AST 从
hud.py 里抠出来现场跑的（不 import 它，那要 PyObjC）。

每个用例断言两件事：
  1. generate() 的返回形状没变（groups / verdict / scores / model / error / elapsed_s）；
  2. 同一份值喂给面板会显示成什么：applyJudgment_ 整个函数照搬着跑（假 self 记下每次
     _render），候选那一列的概率、组内排序也都跑 hud.py 里的原件。

适配层不校验（它只搬键，见 src/generate.py 的说明），所以坏值是**原样递到面板**的，
用例要证明的就是面板拿到坏值不炸：意图不是字符串显示「暂未判断」、风险不是数显示
「风险待判断」、分不是数显示「待定」。范围不夹：上游给 11 分显示「危险 11/9」，这里照旧。
重点是坏形状：风险超范围/不是数、意图不是字符串、judgment 整块没给、reply_scores 没给、
只给了一条候选、截断成半个 JSON、带围栏、模型根本没按 JSON 给（退回逐行读）。
"""
from __future__ import annotations

import ast
import json
import sys
from math import isfinite
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import generate                                          # noqa: E402
import questions                                         # noqa: E402
import styles                                            # noqa: E402

MESSAGE = "这个需求你今天跟一下，明早之前给我结果"
CONTEXT = "王总: 上周那版还没改完\n我: 今天在弄了"
TONES = [styles.DEFAULT_SLOTS[0], styles.DEFAULT_SLOTS[1], styles.NONE_LABEL]


# ---------------------------------------------------------------------------
# 面板那几行：直接跑 src/hud.py 里的源码（不 import，PyObjC 在这台机器上装不了）
# ---------------------------------------------------------------------------

_HUD = ast.parse((ROOT / "src/hud.py").read_text("utf-8"))


def _func_source(name: str) -> str:
    for node in ast.walk(_HUD):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            node = ast.parse(ast.unparse(node)).body[0]
            node.decorator_list = []            # @objc.python_method 这里没有
            return ast.unparse(node)
    raise SystemExit(f"没在 src/hud.py 里找到 {name}()")


class _Palette(dict):
    """PALETTE 的替身：颜色只要能认出是哪一个就行。"""

    def __missing__(self, key):
        self[key] = f"<{key}>"
        return self[key]


_NS: dict = {"isfinite": isfinite, "PALETTE": _Palette()}
for _name in ("_judgment_text", "_risk_level", "_confidence_text", "_actions_text",
              "_prob_text", "applyJudgment_", "_rank_payload"):
    exec(_func_source(_name), _NS)             # noqa: S102 —— 跑的就是 hud.py 的原件

render_choice = _NS["_judgment_text"]
render_prob = _NS["_prob_text"]


class FakePanel:
    """applyJudgment_ / _rank_payload 要的那点 self。记下每次 _render 就够了。"""

    def __init__(self):
        self.rendered: dict[str, tuple] = {}
        self.risk_scale = "<未调用>"
        self._risk_dots = []          # applyJudgment_ 只在面板建好后才点亮那三个点
        self.header = ""
        self._last_intent = None
        self._last_risk = None
        self._stream_rows = {"stale": 1}

    def _show(self):
        pass

    def _render(self, key, text, color=None):
        self.rendered[key] = (text, color)

    def _context_line(self, sender, prev):
        return f"{sender}/{prev}"

    def _set_risk_scale(self, risk):
        self.risk_scale = risk

    def _set_candidate_header(self, text):
        self.header = text


def panel_of(verdict: dict) -> FakePanel:
    """把一份 verdict 喂给 hud.applyJudgment_，返回面板上各行的文字。"""
    panel = FakePanel()
    _NS["applyJudgment_"](panel, (verdict, "王总", "上一条"))
    return panel


def rank(payload: list, scores: dict) -> list:
    return _NS["_rank_payload"](FakePanel(), payload, scores)


# ---------------------------------------------------------------------------
# 一次「模型回答」
# ---------------------------------------------------------------------------

JUDGMENT = {
    "intent": "催我今天出结果", "confidence": 0.78,
    "intent_probs": {"催我今天出结果": 0.78, "只是问问进度": 0.22},
    "risk": 5, "risk_probs": {"4": 0.3, "5": 0.5, "6": 0.2},
    "actions": ["先给当前状态", "给一个明确时间点"],
}
REPLIES = ["今天下班前给你", "在弄了 明早之前一定给"]


def reply(**over) -> str:
    body = {"replies": list(REPLIES), "reply_scores": [0.35, 0.65],
            "judgment": json.loads(json.dumps(JUDGMENT))}
    body.update(over)
    for key in [k for k, v in body.items() if v is None]:
        body.pop(key)
    return json.dumps(body, ensure_ascii=False)


def run(name: str, content, tones=None) -> dict:
    """content: 每次调用回什么——一个字符串给所有话术，或者按**槽位顺序**给一串。

    话术是并发发出去的，回答按到达顺序分配就不可复现了，所以桩按提示词里的那个话术名
    认人（提示词里写的是「{tone}」），每个话术永远拿到属于它的那一份。
    """
    picked = list(tones or TONES)
    active = [t for t in picked if t in styles.PRESETS]
    answers = ([content] * len(active) if isinstance(content, str) else list(content))
    by_tone = dict(zip(active, answers))
    seen = {"calls": 0}

    def fake_post(url, headers, body, timeout):
        # 这里是唯一被替换掉的东西：真正发 HTTP 的那一个函数
        assert "chat/completions" in url or "messages" in url, url
        prompt = body["messages"][0]["content"]
        picked_tone = next(t for t in active if f"「{t}」" in prompt)
        seen["calls"] += 1
        text = by_tone[picked_tone]
        if isinstance(text, Exception):
            raise text
        return {"choices": [{"message": {"content": text}}]}

    gen_obj = generate.Generator(model="offline-model", api="openai")
    gen_obj._creds = ("https://example.invalid/v1", "offline-not-a-key", "offline-model")
    with patch.object(generate, "http_post_json", fake_post), \
            patch.object(generate, "load_credentials",
                         lambda: ("https://example.invalid/v1", "offline-not-a-key",
                                  "offline-model", "test", "openai")):
        out = gen_obj.generate(MESSAGE, "", picked, CONTEXT)
    check_shape(out, picked)
    active = [g for g in out["groups"] if g["texts"]]
    print(f"  {name}: 调用 {seen['calls']} 次 · {len(active)}/{len(out['groups'])} 个话术有候选"
          f" · verdict={'有' if out['verdict'] else '无'} · 分 {len(out['scores'])} 条")
    return out


def check_shape(out: dict, picked: list) -> None:
    """generate() 的返回形状 + hud 会碰的每一样东西。"""
    assert set(out) == {"groups", "model", "verdict", "scores", "error", "elapsed_s"}, set(out)
    active = [t for t in picked if t in styles.PRESETS]
    assert len(out["groups"]) == len(active), out["groups"]
    for group, tone in zip(out["groups"], active):
        assert set(group) == {"slot", "tone", "texts", "scores", "verdict", "error"}, set(group)
        assert group["tone"] == tone and picked[group["slot"]] == tone
        assert len(group["texts"]) <= styles.PER_TONE
        assert all(isinstance(t, str) and t.strip() for t in group["texts"])
    assert isinstance(out["scores"], dict)
    verdict = out["verdict"]
    if verdict is not None:
        assert verdict["message"] == MESSAGE and verdict["backend"] == "offline-model"
        panel = panel_of(verdict)                       # 不炸，就是这一关要的
        assert isinstance(panel.rendered["intent"][0], str) and panel.rendered["intent"][0]
        assert isinstance(panel.rendered["risk"][0], str) and panel.rendered["risk"][0]
    # hud._payload_from_gen 的形状 + 组内排序（跑的是 hud.py 的原件）
    payload = [(g["slot"], g["tone"], [{"text": t, "prob": None} for t in g["texts"]])
               for g in out["groups"] if g["texts"]]
    ranked = rank(payload, out["scores"])
    assert [(s, t) for s, t, _ in ranked] == [(s, t) for s, t, _ in payload]
    for (_s, _t, items), (_s2, _t2, before) in zip(ranked, payload):
        assert sorted(i["text"] for i in items) == sorted(b["text"] for b in before)
        for item in items:
            assert isinstance(render_prob(item["prob"]), str)


def main() -> int:
    print("offline_check —— 合并调用的解析器 + 面板的读法，全程不联网\n")

    print("正常形状:")
    out = run("完整对象", reply())
    assert [g["texts"] for g in out["groups"]] == [REPLIES, REPLIES]
    assert out["scores"] == {REPLIES[0]: 0.35, REPLIES[1]: 0.65}
    verdict = out["verdict"]
    assert verdict["intent"] == "催我今天出结果" and verdict["risk"] == 5
    assert verdict["confidence"] == 0.78            # 原样，连 int→float 都不转
    assert verdict["intent_probs"] == JUDGMENT["intent_probs"]
    panel = panel_of(verdict)
    assert panel.rendered["intent"] == ("催我今天出结果", "<text>")
    assert panel.rendered["confidence"][0] == "意图识别率 78%"
    assert panel.rendered["risk"] == ("● 留神  5/9", "<amber>")
    assert panel.rendered["actions"][0] == "先给当前状态 · 给一个明确时间点"
    assert panel.rendered["status"][0] == "分析完成 · offline-model"
    assert panel.risk_scale == 5 and panel._last_intent == "催我今天出结果"
    assert panel._stream_rows == {}                 # 新一轮候选从第 0 行开始
    # 组内按分重排：第二条分高，排到前面
    ranked = rank([(0, TONES[0], [{"text": t, "prob": None} for t in REPLIES])], out["scores"])
    assert [i["text"] for i in ranked[0][2]] == [REPLIES[1], REPLIES[0]]
    assert render_prob(ranked[0][2][0]["prob"]) == "65%"

    out = run("```json 围栏", "```json\n" + reply() + "\n```")
    assert out["groups"][0]["texts"] == REPLIES and out["verdict"]["risk"] == 5

    out = run("前后带废话", "好的，这是结果：\n" + reply() + "\n希望有帮助！")
    assert out["groups"][0]["texts"] == REPLIES and out["verdict"] is not None

    out = run("judgment 摊在顶层没套 judgment",
              json.dumps({"replies": list(REPLIES), "reply_scores": [0.4, 0.6], **JUDGMENT},
                         ensure_ascii=False))
    assert out["verdict"]["intent"] == "催我今天出结果"

    out = run("分数是 {原文: 分} 而不是数组",
              reply(reply_scores={REPLIES[0]: 0.2, REPLIES[1]: 0.8}))
    assert out["scores"] == {REPLIES[0]: 0.2, REPLIES[1]: 0.8}

    out = run("分数没归一（原样递下去）", reply(reply_scores=[3, 7]))
    assert out["scores"] == {REPLIES[0]: 3, REPLIES[1]: 7}   # 不归一、不转类型
    assert render_prob(7) == "700%"                          # 面板只是照着显示

    print("\n数组分 + 空候选：按模型原序配对，不按 texts 的位置错位")
    print("  （直接跑 generate._scores_by_text，覆盖之前 zip(texts, raw) 会错位的那几种排布）:")
    A, B = "先这样", "后这样"

    # 空候选夹在中间：texts 里空条目已经被丢掉，raw 数组还是模型写的原长度。
    obj = {"replies": [A, "", B], "reply_scores": [0.1, 0.2, 0.3]}
    texts = generate.Generator._replies(obj, "")
    assert texts == [A, B]                                   # 空条目确实被 _replies 丢了
    assert generate._scores_by_text(obj, texts) == {A: 0.1, B: 0.3}   # B 拿自己的 0.3，不是 0.2

    # 空的是第一条
    obj = {"replies": ["", A, B], "reply_scores": [0.1, 0.2, 0.3]}
    texts = generate.Generator._replies(obj, "")
    assert texts == [A, B]
    assert generate._scores_by_text(obj, texts) == {A: 0.2, B: 0.3}

    # 空的是最后一条
    obj = {"replies": [A, B, ""], "reply_scores": [0.1, 0.2, 0.3]}
    texts = generate.Generator._replies(obj, "")
    assert texts == [A, B]
    assert generate._scores_by_text(obj, texts) == {A: 0.1, B: 0.2}

    # {候选原文: 分} 这种写法压根不看 obj["replies"] 的顺序，空候选混在里面不影响它，原样不动
    obj = {"replies": [A, "", B], "reply_scores": {A: 0.1, B: 0.3}}
    texts = generate.Generator._replies(obj, "")
    assert generate._scores_by_text(obj, texts) == {A: 0.1, B: 0.3}

    # {"1": 分, "2": 分} 这种一基下标是按 texts（已经丢空）的位置编号的，跟空候选也没关系
    obj = {"replies": [A, "", B], "reply_scores": {"1": 0.5, "2": 0.6}}
    texts = generate.Generator._replies(obj, "")
    assert generate._scores_by_text(obj, texts) == {A: 0.5, B: 0.6}

    # 模型压根没给 replies 数组（比如截断只剩 reply_scores，或者退回逐行读）：
    # 没有模型原序可依，位置对齐是唯一能做的事，这条路径本来就该保持原样。
    obj = {"reply_scores": [0.7, 0.8]}
    assert "replies" not in obj
    assert generate._scores_by_text(obj, [A, B]) == {A: 0.7, B: 0.8}

    print("\n三个话术都在（并发三次调用，判断取第一份）:")
    a = json.dumps({"replies": ["甲一", "甲二"], "reply_scores": [0.6, 0.4],
                    "judgment": {**JUDGMENT, "intent": "第一份判断"}}, ensure_ascii=False)
    b = json.dumps({"replies": ["乙一", "乙二"], "reply_scores": [0.3, 0.7],
                    "judgment": {**JUDGMENT, "intent": "第二份判断"}}, ensure_ascii=False)
    c = json.dumps({"replies": ["丙一", "丙二"], "reply_scores": [0.5, 0.5],
                    "judgment": {**JUDGMENT, "intent": "第三份判断"}}, ensure_ascii=False)
    three = [styles.DEFAULT_SLOTS[0], styles.DEFAULT_SLOTS[1], styles.labels()[2]]
    out = run("三份判断不一致 → 取槽位最靠前那份", [a, b, c], tones=three)
    assert out["verdict"]["intent"] == "第一份判断"      # 不合并、不投票
    assert [g["texts"] for g in out["groups"]] == [["甲一", "甲二"], ["乙一", "乙二"],
                                                   ["丙一", "丙二"]]
    assert len(out["scores"]) == 6                      # 每条候选都有自己的分

    out = run("第一个话术整个失败 → 判断落到下一个",
              [generate.urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None), b, c],
              tones=three)
    assert out["verdict"]["intent"] == "第二份判断"      # 上游的判断独立于生成，这里保住同一条性质
    assert out["groups"][0]["texts"] == [] and "429" in out["groups"][0]["error"]
    assert out["groups"][1]["texts"] == ["乙一", "乙二"]
    assert out["error"] == ""                           # 还有候选，就不算整体失败

    print("\n坏形状（适配层一律原样递下去，看面板怎么兜）:")
    out = run("风险超范围", reply(judgment={**JUDGMENT, "risk": 11}))
    assert out["verdict"]["risk"] == 11                 # 不夹回 9
    panel = panel_of(out["verdict"])
    assert panel.rendered["risk"] == ("● 危险  11/9", "<red>")   # 上游给 11 也是这样显示
    assert panel.risk_scale == 11

    out = run("风险不是数", reply(judgment={**JUDGMENT, "risk": "很高"}))
    assert out["verdict"]["risk"] == "很高"             # 不丢、不替它编个 0
    panel = panel_of(out["verdict"])
    assert panel.rendered["risk"] == ("风险待判断", "<muted>")
    assert panel.risk_scale is None                     # 风险等级那三个点谁都不亮
    assert panel._last_risk == "很高"                   # YOLO 框那边也自己兜（_risk_level）

    out = run("风险是小数", reply(judgment={**JUDGMENT, "risk": 4.7}))
    assert out["verdict"]["risk"] == 4.7
    assert panel_of(out["verdict"]).rendered["risk"] == ("● 留神  5/9", "<amber>")  # 四舍五入

    out = run("意图不是字符串", reply(judgment={**JUDGMENT, "intent": {"label": "派活"}}))
    assert out["verdict"]["intent"] == {"label": "派活"}
    panel = panel_of(out["verdict"])
    assert panel.rendered["intent"] == ("暂未判断", "<text>")
    assert panel._last_intent == ""                     # 没有可用意图 → YOLO 框不打标签

    out = run("模型回了英文 key（题面里的那 8 个之一）",
              reply(judgment={**JUDGMENT, "intent": "派活"}))
    assert out["verdict"]["intent"] == "派活"           # 不查表、不改写
    assert panel_of(out["verdict"]).rendered["intent"][0] == "派活"

    out = run("没给 confidence", reply(judgment={k: v for k, v in JUDGMENT.items()
                                                 if k != "confidence"}))
    assert "confidence" not in out["verdict"]           # 不补 0.0
    assert panel_of(out["verdict"]).rendered["confidence"][0] == ""   # 整行空着

    out = run("actions 没给", reply(judgment={k: v for k, v in JUDGMENT.items()
                                              if k != "actions"}))
    assert "actions" not in out["verdict"]
    assert panel_of(out["verdict"]).rendered["actions"][0] == ""
    out = run("actions 给了一句话", reply(judgment={**JUDGMENT, "actions": "先认下来"}))
    assert panel_of(out["verdict"]).rendered["actions"][0] == "先认下来"
    out = run("actions 里混了非字符串", reply(judgment={**JUDGMENT, "actions": ["先认", 7, None]}))
    assert out["verdict"]["actions"] == ["先认", 7, None]     # 不丢
    assert panel_of(out["verdict"]).rendered["actions"][0] == "先认"

    out = run("judgment 整块没给", reply(judgment=None))
    assert out["verdict"] is None                       # hud 把它当判断失败，候选照常
    assert out["groups"][0]["texts"] == REPLIES

    out = run("reply_scores 没给", reply(reply_scores=None))
    assert out["scores"] == {}
    ranked = rank([(0, TONES[0], [{"text": t, "prob": None} for t in REPLIES])], out["scores"])
    assert [i["prob"] for i in ranked[0][2]] == [0.0, 0.0]   # 缺分记 0，行不丢
    assert render_prob(0.0) == "0%"

    out = run("分不是数", reply(reply_scores=["高", "低"]))
    assert out["scores"] == {REPLIES[0]: "高", REPLIES[1]: "低"}
    ranked = rank([(0, TONES[0], [{"text": t, "prob": None} for t in REPLIES])], out["scores"])
    assert [i["text"] for i in ranked[0][2]] == REPLIES      # 排不了就保持原序，不炸
    assert render_prob("高") == "待定"

    out = run("只给了一条候选", reply(replies=REPLIES[:1], reply_scores=[1.0]))
    assert out["groups"][0]["texts"] == REPLIES[:1]         # 上游也不补，面板少一行

    out = run("多给了几条（只取前 PER_TONE 条）",
              reply(replies=REPLIES + ["第三条", "第四条"], reply_scores=[0.3, 0.3, 0.2, 0.2]))
    assert out["groups"][0]["texts"] == REPLIES

    cut = '{"replies": ["今天下班前给你", "在弄了 明早之前一定给"], "reply_sc'
    out = run("截断成半个 JSON", cut)
    assert out["groups"][0]["texts"] == REPLIES             # 完整的两条抢出来了
    assert out["verdict"] is None and out["scores"] == {}

    out = run("模型根本没按 JSON 给（退回逐行读）",
              "1. 今天下班前给你\n2. 在弄了 明早之前一定给")
    assert out["groups"][0]["texts"] == REPLIES             # 上游那套逐行读法还在
    assert out["verdict"] is None

    out = run("每行带语气名前缀（上游的剥前缀照旧）",
              json.dumps({"replies": ["稳妥：今天下班前给你", "「在弄了 明早之前一定给」"],
                          "reply_scores": [0.5, 0.5]}, ensure_ascii=False))
    assert out["groups"][0]["texts"] == REPLIES

    out = run("整段是空的", "")
    assert out["groups"][0]["texts"] == [] and out["verdict"] is None
    assert out["error"] == ""      # 空结果不是错误消息，hud 自己报「空结果」

    print("\n题面没被改写:")
    spec = questions.render_judgment_spec()
    for name, desc in questions.INTENTS.items():
        assert f"  {name}: {desc}" in spec
    for i, text in enumerate(questions.RISK_LEVELS):
        assert f"  {i}: {text}" in spec
    assert questions.INTENT_QUESTION in spec and questions.RISK_QUESTION in spec
    assert questions.RANK_QUESTION in spec
    assert spec in generate.PROMPT_ONE.format(
        message="m", context_line="", intent_line="", n=styles.PER_TONE, tone="t",
        instruction="i", judgment_spec=spec,
        # 上游 v0.6.0 给 PROMPT_ONE 加了 {variation}（每话术候选数可配 1–5 带来的）
        variation=generate._variation_instruction(styles.PER_TONE))
    print("  意图 8 项、风险 10 档、两道题干和排序那句都在提示词里，逐字来自 questions.py")

    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
