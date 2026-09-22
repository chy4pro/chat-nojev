# -*- coding: utf-8 -*-
"""差分测试（macOS 版）：证明「模型之后的处理」这一段，我们这版和上游一模一样。

同样的判断值、同样的候选回复、同样的分，上游那条流水线和我们这条必须走出同一个结果：
同一份 verdict、同一份排好序的候选、面板上同一行字。上游的值来自两个模型三次调用
（判断一次、排序一次、每个话术各一次生成）；我们的值来自「每个话术一次」那几次调用本身。
**产出方换了，下游一个字都不该变。**

标准是「同一份值进去，同一份东西出来」：适配层只搬键，不校验、不夹范围、不归一，
所以两边的线格式虽然不同，喂进去的**值**是同一份，出来的东西也就该逐键相同——包括
风险超范围这种坏值（上游的判断模型一样会吐，下游本来就得防着）。

对不上的地方只有两类，都在 expect_diff 里写明原因：
  1. 上游的判断层自己会改值（意图不在那 8 个标签里就退「闲聊」、confidence / risk 转不成
     数就当 0）。这一版不改值——模型写的那句短语就是面板上显示的那句，坏值留给用的地方兜。
     这正是这个项目的论点：分类器只能在闭集里选，通用模型可以直接写出要显示的话。
  2. 判断没出来的时候，上游连排序也一起没了（同一个判断模型），我们的分跟候选一起回来，
     所以还在。

每个场景只写一份中立数据（每个话术两条候选、每条的分、判断那几项），
由两个小函数分别渲染成两种线格式，再分别喂给两边**真正的**代码。

打桩位置（都不碰网络，都只打在最外面那层发 HTTP 的函数上）：
  上游：generate.http_post_json + judge_jev.http_post_json（它是 from generate import 进来的，
        要分别打）。按 URL 分流：/v1/systemone 是判断/排序那两次，/v1/chat/completions 是生成。
        于是上游真正跑的是 JevJudge.judge()（含它那套查表和 float 兜底）、
        JevJudge.rank_candidates()、Generator._parse()。
  我们：只打 generate.http_post_json。真正跑的是 Generator.generate() 全程——并发、
        JSON 解析、适配层，一个都没绕过。

下游那几段（_payload_from_gen / _rank_payload / _finish_generate / applyJudgment_）**不重写**：
用 AST 从各自的 src/hud.py 里把函数源码抠出来现场跑（配一个假 self）。hud.py import 不了，
它要 PyObjC；但跑的确实是两棵树里那几行原件。

两棵树里模块同名（generate / styles / userconfig…），同一个进程里 import 会打架，所以用子进程隔离：
    python3 tools/equivalence_check_macos.py --side upstream --tree <path>
    python3 tools/equivalence_check_macos.py --side ours     --tree <path>
各自把 sys.path 摆好、跑完全部场景、往 stdout 打一份 JSON；
不带 --side 就是驱动器：两边都跑一遍再逐键比对。

上游那一侧走的是**云端判断**（judge_jev.JevJudge）。本地那个 decider-2b 出的 dict 形状、
键、ACTION_MAP 查表都一样，但它要 torch 和 7GB 权重，这个容器里跑不了——而它正是这次
被删掉的东西，见 macos/src/questions.py 顶上的说明。

无第三方依赖（上游 judge.py 顶上 import numpy，所以那一侧需要 numpy 能 import）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# 中立数据
# ---------------------------------------------------------------------------

MESSAGE = "这个需求你今天跟一下，明早之前给我结果"
CONTEXT = "王总: 上周那版还没改完\n我: 今天在弄了"
SENDER = "王总"
PREV = "上周那版还没改完"

# 话术名按槽位取（两棵树的 src/styles.py 逐字相同，这里只按下标引用，不重打标签）
SLOT_A, SLOT_B, SLOT_C = 0, 1, 2

FULL_JUDGMENT = {
    "intent": "派活",
    "confidence": 0.72,
    "intent_probs": {"派活": 0.72, "催进度": 0.18, "闲聊": 0.10},
    "risk": 4,
    "risk_probs": {"3": 0.2, "4": 0.5, "5": 0.3},
    # actions：上游按意图查 judge.ACTION_MAP，我们让模型自己写。意图落在那 8 个标签上时
    # 两边给的是同一份（模型照着题面里的示例写），这里就写那一份——对得上才有比对的意义。
    "actions": ["接住", "问清交付标准和期限", "先给个时间点"],
}

TONE_A_REPLIES = ["收到，今天下班前给你", "行，我这就去看，晚点回你"]
TONE_B_REPLIES = ["有一说一 这活我接了", "绷不住了 这就去整"]
TONE_C_REPLIES = ["明早九点前给你", "今天做不完，明早九点前一定给"]


def judgment(**over):
    out = json.loads(json.dumps(FULL_JUDGMENT))
    for name, value in over.items():
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out


def scenario(name, note, *, tones=(SLOT_A, SLOT_B), replies=None, scores=None,
             judgment_values=None, judge_http_error=None, gen_http_error=(),
             drop_scores=(), expect_diff=()):
    replies = replies or {SLOT_A: TONE_A_REPLIES, SLOT_B: TONE_B_REPLIES}
    flat = [t for slot in tones for t in replies.get(slot, [])]
    if scores is None:
        scores = {t: round(0.9 - 0.15 * i, 2) for i, t in enumerate(flat)}
    return {
        "name": name,
        "note": note,
        "tones": list(tones),
        "replies": {str(k): list(v) for k, v in replies.items()},
        "scores": scores,               # {候选原文: 分}
        "judgment": FULL_JUDGMENT if judgment_values is None else judgment_values,
        "judge_http_error": judge_http_error,   # 上游：判断那次调用回这个状态码
        "gen_http_error": list(gen_http_error),  # 两边：这些槽位的生成调用回 500
        "drop_scores": list(drop_scores),        # 这些候选两边都不给分
        "expect_diff": list(expect_diff),
    }


SCENARIOS = [
    scenario("ordinary_two_tones", "普通情况：两个话术各两条候选"),
    scenario("three_tones", "三个话术：上游三次生成 + 一次判断 + 一次排序；我们三次调用",
             tones=(SLOT_A, SLOT_B, SLOT_C),
             replies={SLOT_A: TONE_A_REPLIES, SLOT_B: TONE_B_REPLIES, SLOT_C: TONE_C_REPLIES}),
    scenario("second_candidate_wins_inside_a_tone",
             "组内第二条分更高：两边都必须把它排到第一",
             scores={TONE_A_REPLIES[0]: 0.2, TONE_A_REPLIES[1]: 0.8,
                     TONE_B_REPLIES[0]: 0.1, TONE_B_REPLIES[1]: 0.9}),
    scenario("boundary_values_low", "边界值：风险 0、把握 0",
             judgment_values=judgment(risk=0, confidence=0.0,
                                      risk_probs={"0": 0.9, "1": 0.1})),
    scenario("boundary_values_high", "边界值：风险 9、把握 1",
             judgment_values=judgment(risk=9, confidence=1.0,
                                      risk_probs={"8": 0.1, "9": 0.9})),
    scenario("risk_is_a_decimal", "风险是小数（上游本地模型给的是均值）：两边都四舍五入着显示",
             judgment_values=judgment(risk=4.7)),
    scenario("risk_outside_its_range",
             "风险 11（超出 0..9）：两边都原样递下去，面板显示「危险 11/9」，谁也不夹回 9",
             judgment_values=judgment(risk=11, risk_probs={"9": 1.0})),
    scenario("risk_not_a_number",
             "风险不是数：上游判断层把它换成 0.0（面板显示「安全 0/9」），"
             "我们原样递下去、面板显示「风险待判断」——不替模型说「安全」",
             judgment_values=judgment(risk="很高"),
             expect_diff=("verdict.risk", "panel.risk", "panel_risk_scale", "panel_last_risk")),
    scenario("intent_inside_the_taxonomy",
             "意图正好是题面里的标签：连 actions 都一模一样（上游查表，我们让模型照着示例写）"),
    scenario("intent_outside_the_taxonomy",
             "题面里没有的说法：上游退成「闲聊」并按「闲聊」查 actions，"
             "我们原样显示模型写的那句——这一条就是这个项目的论点",
             judgment_values=judgment(intent="想确认我到底跟没跟",
                                      intent_probs={"想确认我到底跟没跟": 0.8, "派活": 0.2},
                                      actions=["先说清现在到哪了", "给个明确时间点"]),
             expect_diff=("verdict.intent", "verdict.actions", "panel.intent",
                          "panel.actions", "panel_last_intent")),
    scenario("intent_is_an_empty_string",
             "意图给了个空串：上游退「闲聊」，我们显示「暂未判断」",
             judgment_values=judgment(intent="", intent_probs={}, actions=[]),
             expect_diff=("verdict.intent", "verdict.actions", "panel.intent",
                          "panel.actions", "panel_last_intent")),
    scenario("confidence_omitted",
             "没给把握：上游补 0.0（面板显示「意图识别率 0%」），我们不补、那一行空着",
             judgment_values=judgment(confidence=None),
             expect_diff=("verdict.confidence", "panel.confidence")),
    scenario("probabilities_not_normalized",
             "概率加起来不是 1、候选的分是 0~10：两边都原样递下去，谁也不归一",
             scores={TONE_A_REPLIES[0]: 8, TONE_A_REPLIES[1]: 3,
                     TONE_B_REPLIES[0]: 6, TONE_B_REPLIES[1]: 1},
             judgment_values=judgment(intent_probs={"派活": 0.9, "催进度": 0.7},
                                      risk_probs={"4": 2.0})),
    scenario("one_candidate_has_no_score",
             "有一条候选没给分：两边都记 0，行不丢",
             drop_scores=[TONE_B_REPLIES[1]]),
    scenario("one_tone_fails_generation",
             "一个话术的生成调用挂了：另一个话术照常出候选，判断照常（上游是独立那次调用，"
             "我们是从活下来的那次调用里取）",
             gen_http_error=[SLOT_A]),
    scenario("judgment_fails_entirely_generation_succeeds",
             "判断整个没出来、生成成功：两边都不画判断、候选照常上屏；"
             "但上游的排序跟着判断一起没了（同一个模型），我们的分跟候选一起回来所以还在",
             judge_http_error=500,
             expect_diff=("final_candidates",)),
]


# ---------------------------------------------------------------------------
# 渲染成两种线格式
# ---------------------------------------------------------------------------

def scenario_scores(scn) -> dict:
    return {t: v for t, v in scn["scores"].items() if t not in scn["drop_scores"]}


def upstream_judge_answers(scn) -> dict:
    """中立数据 → Jev 判断接口回的 answers（judge_jev.JevJudge.judge 读的那个形状）。"""
    j = scn["judgment"]
    intent = {"choice": j.get("intent"), "probabilities": j.get("intent_probs") or {}}
    if "confidence" in j:
        intent["confidence"] = j["confidence"]
    return {"answers": {
        "intent": intent,
        "risk": {"score": j.get("risk"), "probabilities": j.get("risk_probs") or {}},
    }}


def upstream_rank_answers(scn, candidates) -> dict:
    """中立数据 → Jev 排序那道题回的 answers（rank_candidates 读的那个形状）。"""
    probs = {c: v for c, v in scenario_scores(scn).items() if c in candidates}
    best = max(probs, key=probs.get) if probs else None
    return {"answers": {"best": {"choice": best, "probabilities": probs}}}


def upstream_gen_text(scn, slot) -> str:
    """中立数据 → 生成那次调用回的纯文本（上游就是一行一条）。"""
    return "\n".join(scn["replies"][str(slot)])


def ours_gen_text(scn, slot) -> str:
    """中立数据 → 合并那次调用回的原始文本（模型会怎么打就怎么打：一个 JSON 对象）。"""
    replies = scn["replies"][str(slot)]
    scores = scenario_scores(scn)
    obj = {"replies": list(replies)}
    if any(r in scores for r in replies):
        obj["reply_scores"] = {r: scores[r] for r in replies if r in scores}
    if scn["judge_http_error"] is None:
        obj["judgment"] = json.loads(json.dumps(scn["judgment"]))
    # 模型是带着 ```json 围栏吐出来的，照着来
    return "```json\n" + json.dumps(obj, ensure_ascii=False, indent=1) + "\n```"


# ---------------------------------------------------------------------------
# 从 src/hud.py 里抠出下游那几段（不 import：要 PyObjC）
# ---------------------------------------------------------------------------

class Palette(dict):
    """PALETTE 的替身：颜色只要能认出是哪一个。"""

    def __missing__(self, key):
        self[key] = f"<{key}>"
        return self[key]


def hud_namespace(tree: str, names, extra=None) -> dict:
    """把 hud.py 里这几个函数（连同它们要的模块级小函数）抠出来跑在一个干净的名字空间里。"""
    import math
    import time as _time

    source = open(os.path.join(tree, "src/hud.py"), encoding="utf-8").read()
    module = ast.parse(source)
    ns: dict = {"PALETTE": Palette(), "isfinite": math.isfinite, "time": _time,
                "_log": lambda *a, **k: None, "styles": None}
    ns.update(extra or {})
    found = {}
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            copy = ast.parse(ast.unparse(node)).body[0]
            copy.decorator_list = []          # @objc.python_method / @staticmethod
            found[node.name] = ast.unparse(copy)
    missing = set(names) - set(found)
    assert not missing, f"{tree}/src/hud.py 里没找到 {sorted(missing)}"
    for name in names:
        exec(found[name], ns)                 # noqa: S102 —— 跑的就是 hud.py 的原件
    return ns


class FakePanel:
    """下游那几段要的那点 self：记下每次 _push 和每次 _render。"""

    def __init__(self, ns, judge=None):
        self._ns = ns
        self.judge = judge
        self.pushes: list = []
        self.rendered: dict = {}
        self.risk_scale = None
        self.header = ""
        self._last_intent = None
        self._last_risk = None
        self._risk_dots = [1, 2, 3]
        self._stream_rows = {"stale": 1}
        import threading
        self._model_lock = threading.Lock()

    # —— 被抠出来的那几段函数当方法用
    def __getattr__(self, name):
        func = self._ns.get(name)
        if func is None:
            raise AttributeError(name)
        return lambda *a, **k: func(self, *a, **k)

    def _reply_current(self):
        return True

    def _show(self):
        pass

    def _push(self, selector, payload=None):
        self.pushes.append((selector, payload))

    def _render(self, key, text, color=None):
        self.rendered[key] = [text, color]

    def _context_line(self, sender, prev):
        return f"{sender} · {prev}"

    def _set_risk_scale(self, risk):
        self.risk_scale = risk

    def _set_candidate_header(self, text):
        self.header = text


def panel_snapshot(panel: FakePanel, verdict) -> dict:
    """两边都要交出来的那份东西。"""
    candidates = [p for s, p in panel.pushes if s == "applyCandidates:"]
    return {
        "verdict": verdict,
        "final_candidates": candidates[-1] if candidates else None,
        "push_selectors": [s for s, _ in panel.pushes],
        "errors": [p for s, p in panel.pushes if s == "applyError:"],
        "panel": panel.rendered or None,
        "panel_risk_scale": panel.risk_scale,
        "panel_last_intent": panel._last_intent,
        "panel_last_risk": panel._last_risk,
        "panel_header": panel.header,
    }


# ---------------------------------------------------------------------------
# 跑上游那一侧
# ---------------------------------------------------------------------------

def run_upstream(tree: str) -> dict:
    sys.path.insert(0, os.path.join(tree, "src"))
    os.environ["TYPESAFE_API_KEY"] = "equivalence-check-not-a-real-key"
    import urllib.error

    import generate
    import judge
    import judge_jev
    import styles

    tones = styles.labels()
    ns = hud_namespace(tree, ("_payload_from_gen", "_rank_payload", "_finish_generate",
                              "applyJudgment_"), extra={"styles": styles})

    out = {}
    for scn in SCENARIOS:
        picked = [styles.NONE_LABEL] * styles.MAX_SLOTS
        for slot in scn["tones"]:
            picked[slot] = tones[slot]
        calls = {"judge": 0, "rank": 0, "gen": 0}
        by_prompt_slot = {}

        def fake_post(url, headers, body, timeout, _scn=scn, _picked=picked,
                      _calls=calls, _slots=by_prompt_slot):
            if "systemone" in url:
                if "best" in (body.get("questions") or {}):
                    _calls["rank"] += 1
                    texts = list((body["questions"]["best"]["criteria"] or {}))
                    return upstream_rank_answers(_scn, texts)
                _calls["judge"] += 1
                if _scn["judge_http_error"]:
                    raise urllib.error.HTTPError(url, _scn["judge_http_error"], "boom",
                                                 {}, None)
                return upstream_judge_answers(_scn)
            assert "chat/completions" in url, url
            _calls["gen"] += 1
            prompt = body["messages"][0]["content"]
            slot = next(i for i, t in enumerate(_picked)
                        if t in styles.PRESETS and f"「{t}」" in prompt)
            if slot in _scn["gen_http_error"]:
                raise urllib.error.HTTPError(url, 500, "boom", {}, None)
            return {"choices": [{"message": {"content": upstream_gen_text(_scn, slot)}}]}

        generate.http_post_json = judge_jev.http_post_json = fake_post
        generate.load_credentials = lambda: ("https://example.invalid/v1", "k",
                                             "upstream-model", "test", "openai")
        gen_obj = generate.Generator(model="upstream-model", api="openai")
        # —— 这里开始是上游 hud._analyze 的流程，逐句照着搬（判断一次、生成 N 次、排序一次）
        the_judge = judge_jev.JevJudge(base="https://api.typesafe.ai", key="k",
                                       model="jev-latest")
        assert judge.ACTION_MAP is judge_jev.ACTION_MAP    # actions 是查这张表来的
        panel = FakePanel(ns, judge=the_judge)
        try:
            verdict = the_judge.judge(MESSAGE, context=CONTEXT)
        except Exception as exc:
            verdict = None
            panel._push("applyError:", f"判断失败: {type(exc).__name__}")
        gen = gen_obj.generate(MESSAGE, "", picked, CONTEXT)
        if verdict is not None:
            ns["applyJudgment_"](panel, (verdict, SENDER, PREV))
        import types as _types
        newest = _types.SimpleNamespace(text=MESSAGE, sender=SENDER)
        ns["_finish_generate"](panel, gen, newest, 0.0, verdict)
        snapshot = panel_snapshot(panel, verdict)
        snapshot["calls"] = calls
        snapshot["gen_error"] = gen.get("error")
        out[scn["name"]] = snapshot
    return out


# ---------------------------------------------------------------------------
# 跑我们这一侧
# ---------------------------------------------------------------------------

def run_ours(tree: str) -> dict:
    sys.path.insert(0, os.path.join(tree, "src"))
    import urllib.error

    import generate
    import styles

    tones = styles.labels()
    ns = hud_namespace(tree, ("_judgment_text", "_risk_level", "_confidence_text",
                              "_actions_text", "_prob_text", "_payload_from_gen",
                              "_rank_payload", "_finish_generate", "applyJudgment_"),
                       extra={"styles": styles})

    out = {}
    for scn in SCENARIOS:
        picked = [styles.NONE_LABEL] * styles.MAX_SLOTS
        for slot in scn["tones"]:
            picked[slot] = tones[slot]
        calls = {"judge": 0, "rank": 0, "gen": 0}

        def fake_post(url, headers, body, timeout, _scn=scn, _picked=picked, _calls=calls):
            assert "chat/completions" in url, url      # 判断没有自己的端点了
            _calls["gen"] += 1
            prompt = body["messages"][0]["content"]
            slot = next(i for i, t in enumerate(_picked)
                        if t in styles.PRESETS and f"「{t}」" in prompt)
            if slot in _scn["gen_http_error"]:
                raise urllib.error.HTTPError(url, 500, "boom", {}, None)
            return {"choices": [{"message": {"content": ours_gen_text(_scn, slot)}}]}

        generate.http_post_json = fake_post
        generate.load_credentials = lambda: ("https://example.invalid/v1", "k",
                                             "ours-model", "test", "openai")
        gen_obj = generate.Generator(model="ours-model", api="openai")
        # —— 这里开始是我们 hud._analyze 的流程，逐句照着搬（就一次调用，判断在里面）
        panel = FakePanel(ns)
        gen = gen_obj.generate(MESSAGE, "", picked, CONTEXT)
        verdict = gen.get("verdict")
        if verdict is not None:
            ns["applyJudgment_"](panel, (verdict, SENDER, PREV))
        else:
            panel._push("applyError:", "判断失败: 模型这次没给出意图/风险")
        ns["_finish_generate"](panel, gen, 0.0)
        snapshot = panel_snapshot(panel, verdict)
        snapshot["calls"] = calls
        snapshot["gen_error"] = gen.get("error")
        out[scn["name"]] = snapshot
    return out


# ---------------------------------------------------------------------------
# 比对
# ---------------------------------------------------------------------------

# 这几样按要求不参与比对：产出方的名字，以及带着它的那行状态。
EXCLUDED = ("verdict.backend", "panel.status", "push_selectors", "calls", "errors")


def flatten(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out or {prefix: {}}
    if isinstance(value, (list, tuple)):
        out = {}
        for i, v in enumerate(value):
            out.update(flatten(v, f"{prefix}[{i}]"))
        return out or {prefix: []}
    return {prefix: value}


def same_number(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and a == b


def covered(key: str, expected) -> bool:
    return any(key == p or key.startswith(p + ".") or key.startswith(p + "[")
               for p in expected)


def compare(up: dict, ours: dict) -> dict:
    a = flatten({k: v for k, v in up.items() if k not in EXCLUDED})
    b = flatten({k: v for k, v in ours.items() if k not in EXCLUDED})
    a = {k: v for k, v in a.items() if not covered(k, EXCLUDED)}
    b = {k: v for k, v in b.items() if not covered(k, EXCLUDED)}
    diffs, repr_only = [], []
    for key in sorted(set(a) | set(b)):
        ma, mb = key in a, key in b
        if ma and mb:
            va, vb = a[key], b[key]
            if va == vb or same_number(va, vb):
                if type(va) is not type(vb) and isinstance(va, (int, float)):
                    repr_only.append({"key": key, "upstream": repr(va), "ours": repr(vb)})
                continue
            diffs.append({"key": key, "upstream": va, "ours": vb})
        else:
            diffs.append({"key": key,
                          "upstream": a[key] if ma else "<absent>",
                          "ours": b[key] if mb else "<absent>"})
    return {"diffs": diffs, "repr_only": repr_only}


def drive(python: str, upstream_tree: str, ours_tree: str) -> int:
    here = os.path.abspath(__file__)

    def side(name, tree):
        proc = subprocess.run([python, here, "--side", name, "--tree", tree],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            sys.stderr.write(f"--- {name} 跑挂了 ---\n{proc.stdout}\n{proc.stderr}\n")
            raise SystemExit(2)
        return json.loads(proc.stdout)

    up, ours = side("upstream", upstream_tree), side("ours", ours_tree)

    width = max(len(s["name"]) for s in SCENARIOS) + 2
    print("场景".ljust(width - 4) + "  结果   说明")
    print("-" * (width + 66))
    failures, expected = 0, 0
    details = []
    for scn in SCENARIOS:
        name = scn["name"]
        cmp_ = compare(up[name], ours[name])
        keys = [d["key"] for d in cmp_["diffs"]]
        if not keys:
            verdict, mark = "PASS", ""
        elif all(covered(k, scn["expect_diff"]) for k in keys):
            verdict, mark = "PASS*", "已知并解释的分歧: " + ", ".join(keys)
            expected += 1
        else:
            verdict, mark = "FAIL", "未预期的分歧: " + ", ".join(
                k for k in keys if not covered(k, scn["expect_diff"]))
            failures += 1
        print(name.ljust(width) + verdict.ljust(7) + (mark or scn["note"])[:110])
        if cmp_["diffs"]:
            details.append((name, verdict, cmp_["diffs"]))
    print("-" * (width + 66))
    print(f"共 {len(SCENARIOS)} 个场景：{len(SCENARIOS) - failures - expected} 完全一致，"
          f"{expected} 有已知分歧，{failures} 失败")

    if details:
        print("\n差异明细（键 / 上游 / 我们）:")
        for name, verdict, diffs in details:
            print(f"\n  [{verdict}] {name}")
            for d in diffs:
                print(f"    {d['key']}\n      上游: {d['upstream']!r}\n      我们: {d['ours']!r}")

    repr_keys = {}
    for scn in SCENARIOS:
        for d in compare(up[scn["name"]], ours[scn["name"]])["repr_only"]:
            repr_keys.setdefault(d["key"], set()).add((d["upstream"], d["ours"]))
    if repr_keys:
        print("\n数值相等但 int/float 表示不同（不算分歧）:")
        for key, pairs in sorted(repr_keys.items()):
            for a, b in sorted(pairs):
                print(f"    {key}: 上游 {a} / 我们 {b}")

    first = SCENARIOS[0]["name"]
    print("\n不参与比对的几样（按要求；两边分别是）:")
    print(f"    verdict.backend: 上游 {up[first]['verdict']['backend']!r}"
          f" / 我们 {ours[first]['verdict']['backend']!r}   ← 产出方的名字，必然不同")
    print(f"    面板状态行:      上游 {up[first]['panel']['status'][0]!r}"
          f" / 我们 {ours[first]['panel']['status'][0]!r}   ← 它显示的就是 backend")
    print(f"    上屏次数:        上游 {up[first]['push_selectors']}"
          f"\n                     我们 {ours[first]['push_selectors']}"
          "\n                     ← 上游先推一次没排序的（显示「排序中」）、排完再推一次；"
          "我们的分跟候选一起回来，只推一次（少一次重绘，内容相同）")

    print("\n每个场景的调用次数（证明没打网络、也没多打）:")
    print(f"    {'场景'.ljust(width - 4)}  上游(判断/排序/生成)   我们(生成)")
    for scn in SCENARIOS:
        c_up, c_ours = up[scn["name"]]["calls"], ours[scn["name"]]["calls"]
        counts = f"{c_up['judge']}/{c_up['rank']}/{c_up['gen']}"
        print(f"    {scn['name'].ljust(width)}{counts.ljust(23)}{c_ours['gen']}")
    total_up = sum(sum(up[s["name"]]["calls"].values()) for s in SCENARIOS)
    total_ours = sum(sum(ours[s["name"]]["calls"].values()) for s in SCENARIOS)
    print(f"    合计：上游 {total_up} 次 / 我们 {total_ours} 次")

    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", choices=("upstream", "ours"))
    ap.add_argument("--tree", default=None)
    ap.add_argument("--upstream", default=os.environ.get("UPSTREAM_TREE"))
    ap.add_argument("--ours", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "macos"))
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    if args.side == "upstream":
        json.dump(run_upstream(args.tree), sys.stdout, ensure_ascii=False)
        return 0
    if args.side == "ours":
        json.dump(run_ours(args.tree), sys.stdout, ensure_ascii=False)
        return 0
    if not args.upstream:
        ap.error("要给 --upstream <上游那棵树> 或设 UPSTREAM_TREE")
    return drive(args.python, args.upstream, args.ours)


if __name__ == "__main__":
    raise SystemExit(main())
