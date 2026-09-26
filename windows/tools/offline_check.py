# -*- coding: utf-8 -*-
"""不联网的合并调用自测：把一串「模型可能吐出来的东西」喂给解析器，看 analyze() 还能不能用。

    python tools/offline_check.py

只替换 core/draft.py 里那个 chat()（唯一出网的地方），其余整条链都是真的。
每个用例断言两件事：
  1. analyze() 的返回形状没变（candidates / best_index / best_reply / scores / answers / usage / reply_to）；
  2. 同一份值喂给 app/overlay.py 会显示成什么——_choice() 整个函数、紧张度那行的判据和文案
     都直接从 overlay.py 源码里抠出来跑，不 import 它（那要 PySide6）。UI 的排序键也照抄过来跑一遍。

适配器不校验（它只搬键，见 core/draft.py 的说明），所以坏值是**原样递到界面**的，
用例要证明的就是界面拿到坏值不炸：不是字符串的 choice 显示「暂未判断」，
不是 0..9 的紧张度显示「紧张度待判断」——跟上游收到判断模型的垃圾时一模一样。
重点是坏形状：分数超范围/不是数字、choice 不是字符串、整块排序没给、只给了两条、
截断成半个 JSON、带围栏。
"""
from __future__ import annotations

import ast
import json
import os
import sys
from math import isfinite
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import draft
from core.engine import analyze
from core.errors import redact_secrets
from core.providers import LEGACY, LLM_ENV
from core.questions import CHOICE_OPTIONS, JUDGE_QUESTIONS, SCORE_MAX

MESSAGES = [
    ("her", "你今天是不是又忘了我跟你说过什么？"),
    ("me", "记得，你先别提示我，让我自己说。"),
    ("her", "那你说。"),
]
RELATIONSHIP = "romantic partners"


def literal(path: str, name: str):
    """从一个 .py 里抠出某个模块级字面量赋值（不 import：overlay 要 PySide6，这里不装）。"""
    source = (Path(__file__).resolve().parent.parent / path).read_text("utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return ast.literal_eval(node.value)
    raise SystemExit(f"没在 {path} 里找到 {name}")


def _overlay() -> ast.Module:
    return ast.parse((Path(__file__).resolve().parent.parent / "app/overlay.py").read_text("utf-8"))


def _func_source(tree, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise SystemExit(f"没在 app/overlay.py 里找到 {name}()")


def _assigned_expr(tree, name: str) -> str:
    """某个赋值右边的表达式源码（valid_score 是方法体里的局部变量，所以要 walk）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return ast.unparse(node.value)
    raise SystemExit(f"没在 app/overlay.py 里找到 {name} 的赋值")


def _settext_expr(tree, needle: str) -> str:
    """某个 xxx.setText(...) 的实参源码（按文案里的关键词认，紧张度那行）。"""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setText" and node.args):
            src = ast.unparse(node.args[0])
            if needle in src:
                return src
    raise SystemExit(f"没在 app/overlay.py 里找到带「{needle}」的 setText")


# 界面那三样：_choice() 整个函数照搬着跑，紧张度的判据和文案照搬它的表达式。
# 这里不 import overlay（要 PySide6），但跑的确实是它源码里的那几行。
_OVERLAY = _overlay()
_NS: dict = {}
exec(_func_source(_OVERLAY, "_choice"), _NS)
render_choice = _NS["_choice"]
_VALID_SCORE = _assigned_expr(_OVERLAY, "valid_score")
_TENSION_TEXT = _settext_expr(_OVERLAY, "紧张度")


def render_tension(score):
    """overlay 拿到这个 score 会把紧张度那行写成什么（坏值走它自己的退路，不炸）。"""
    valid = eval(_VALID_SCORE, {"isfinite": isfinite, "score": score})
    return eval(_TENSION_TEXT, {"score": score, "valid_score": valid})


def check_shape(result: dict, expect_cands: int) -> None:
    """engine 的返回形状 + overlay.show() 会碰的每一样东西。"""
    assert set(result) == {"candidates", "best_index", "best_reply", "scores", "answers",
                           "usage", "reply_to"}, set(result)
    cands = result["candidates"]
    assert isinstance(cands, list) and len(cands) == expect_cands, cands
    assert result["best_index"] in range(len(cands))          # overlay 会再 guard 一次
    assert result["best_reply"] == cands[result["best_index"]]
    assert len(result["scores"]) == 3 and all(isinstance(s, float) for s in result["scores"])
    answers = result["answers"]
    # overlay._choice()：模型写的那句短语原样显示；缺了 / 空的 / 不是字符串才「暂未判断」
    for name in CHOICE_OPTIONS:
        raw = (answers.get(name) or {}).get("choice")
        text = render_choice(answers, name)
        expect = raw.strip() if isinstance(raw, str) and raw.strip() else "暂未判断"
        assert text == expect and isinstance(text, str) and text, (name, raw, text)
    # overlay 的紧张度：0~9 的有限数字才显示，否则「紧张度待判断」（原表达式在 render_tension 里跑）
    score = (answers.get("danger_level") or {}).get("score")
    valid = (isinstance(score, (int, float)) and isfinite(score)
             and 0 <= score <= SCORE_MAX["danger_level"])
    tension = render_tension(score)
    assert tension == (f"紧张度 {score:.0f}/9" if valid else "紧张度待判断"), (score, tension)
    for name, answer in answers.items():
        if name in JUDGE_QUESTIONS:
            assert answer["type"] == JUDGE_QUESTIONS[name]["type"], name
    # overlay.show() 的排序键，原样照抄，跑得通就行
    best = result["best_index"]
    scores = [result["scores"][i] if i < len(result["scores"]) else None for i in range(len(cands))]
    if not any(scores):
        scores = [None] * len(cands)
    order = sorted(range(len(cands)), key=lambda i: (i != best, -(scores[i] or 0), i))
    assert sorted(order) == list(range(len(cands)))
    assert order[0] == best


# choice 那三道题现在答的是一句短语（用对话那门语言写，界面原样显示），不是英文 key
JUDGMENT = {
    "literal_question": {"noul": 0.05},
    "true_intent": {"choice": "希望确认你在意", "confidence": 0.82,
                    "probabilities": {"希望确认你在意": 0.82, "表达不满或受伤": 0.18}},
    "danger_level": {"score": 6, "confidence": 0.6, "probabilities": {"6": 0.6, "7": 0.4}},
    "should_reply_now": {"noul": 0.2},
    "best_action": {"choice": "先核对聊天记录", "confidence": 0.7},
    "she_needs": {"choice": "关注与在意", "confidence": 0.66},
    "tension_resolved": {"noul": 0.08},
}
REPLIES = ["等我想想", "你别急 我这就说", "上周三你说的那家店对吧"]


def reply(**over) -> str:
    body = {"replies": list(REPLIES), "best_reply": "reply_b",
            "reply_scores": {"reply_a": 0.2, "reply_b": 0.65, "reply_c": 0.15},
            "judgment": json.loads(json.dumps(JUDGMENT))}
    body.update(over)
    return json.dumps(body, ensure_ascii=False)


def run(name: str, replies: list, expect_cands: int = 3) -> dict:
    """replies: 第一次调用、（如果有）追问那次调用各返回什么；字符串照发，异常就抛。"""
    calls = iter(replies)

    def fake_chat(*a, **kw):
        try:
            value = next(calls)
        except StopIteration:
            raise AssertionError(f"{name}: 调用次数超了")
        if isinstance(value, Exception):
            raise value
        return value

    with patch.object(draft, "chat", fake_chat):
        result = analyze(MESSAGES, RELATIONSHIP)
    check_shape(result, expect_cands)
    print(f"  {name}: best={result['best_index']} scores="
          f"[{', '.join(f'{s:.2f}' for s in result['scores'])}] "
          f"answers={sorted(result['answers'])}")
    return result


def main() -> int:
    os.environ[LLM_ENV] = "offline-not-a-key"
    print("offline_check —— 合并调用的解析器，全程不联网\n")

    print("脱敏（REDACT_ENV 比 LEGACY 宽，OPENROUTER_API_KEY 只在前者里）:")
    os.environ[LLM_ENV] = "llm-secret-456"
    os.environ["OPENROUTER_API_KEY"] = "or-secret-123"
    text = "debug: LLM_API_KEY=llm-secret-456 OPENROUTER_API_KEY=or-secret-123 done"
    redacted = redact_secrets(text)
    assert "or-secret-123" not in redacted and "llm-secret-456" not in redacted, redacted
    del os.environ["OPENROUTER_API_KEY"]
    os.environ[LLM_ENV] = "offline-not-a-key"     # 恢复成下面用例要的占位值
    # LEGACY 依然不该收 OPENROUTER_API_KEY：那是从 jev-chat-windows 升上来的机器上可能还留着
    # 的判断接口 key，回退到它去调别家的起草接口只会更糟，比没有 key 还差。REDACT_ENV 可以比
    # LEGACY 宽（日志里万一带出来还是要遮），但两张表不能合并——这条断言防的就是以后有人
    # "整理" 代码时把它们并回一张。
    assert "OPENROUTER_API_KEY" not in LEGACY.values()

    print("\n正常形状:")
    r = run("完整对象", [reply()])
    assert r["candidates"] == REPLIES and r["best_index"] == 1
    assert r["scores"] == [0.2, 0.65, 0.15] and r["usage"] == {}
    assert len(r["answers"]) == len(JUDGE_QUESTIONS) + 1  # 7 道题 + best_reply
    assert r["answers"]["true_intent"] == {
        "type": "choice", "choice": "希望确认你在意", "confidence": 0.82,
        "probabilities": {"希望确认你在意": 0.82, "表达不满或受伤": 0.18}}
    assert r["answers"]["danger_level"]["score"] == 6       # 原样，连 int→float 都不转
    assert r["answers"]["should_reply_now"] == {"type": "noul", "noul": 0.2}
    # 界面显示的就是模型写的那句话
    assert render_choice(r["answers"], "true_intent") == "希望确认你在意"
    assert render_tension(r["answers"]["danger_level"]["score"]) == "紧张度 6/9"
    # 模型没给 probabilities 的字段：不补空表，那个键直接不出现
    assert r["answers"]["best_action"] == {"type": "choice", "choice": "先核对聊天记录",
                                           "confidence": 0.7}

    r = run("```json 围栏", ["```json\n" + reply() + "\n```"])
    assert r["candidates"] == REPLIES and r["best_index"] == 1

    r = run("前后带废话", ["好的，这是结果：\n" + reply() + "\n希望有帮助！"])
    assert r["best_index"] == 1

    r = run("分数不归一（原样递下去）",
            [reply(reply_scores={"reply_a": 2, "reply_b": 6, "reply_c": 2})])
    assert r["scores"] == [2.0, 6.0, 2.0]   # 不归一：比例是模型自己报的，界面只拿它排序

    r = run("点名和最高分打架", [reply(best_reply="reply_c")])
    assert r["best_index"] == 2 and r["scores"][1] > r["scores"][2]  # 以点名为准

    print("\n坏形状（适配器一律原样递下去，看界面怎么兜）:")
    bad = json.loads(json.dumps(JUDGMENT))
    bad["true_intent"]["choice"] = "confirm_you_care"   # 模型没听话，回了个英文 key
    bad["she_needs"] = {"choice": "她想要我哄一下"}      # 题目里没有的说法，照样是句人话
    r = run("模型自己造的说法", [reply(judgment=bad)])
    assert r["answers"]["true_intent"]["choice"] == "confirm_you_care"   # 不丢、不改
    assert r["answers"]["she_needs"]["choice"] == "她想要我哄一下"
    assert render_choice(r["answers"], "true_intent") == "confirm_you_care"  # 界面原样显示
    assert render_choice(r["answers"], "she_needs") == "她想要我哄一下"
    assert render_choice(r["answers"], "best_action") == "先核对聊天记录"    # 别的字段不受牵连
    assert render_choice({}, "true_intent") == "暂未判断"                   # 真没给才是「暂未判断」
    assert render_choice({"true_intent": {"type": "choice", "choice": "  "}},
                         "true_intent") == "暂未判断"

    bad = json.loads(json.dumps(JUDGMENT))
    bad["true_intent"] = {"choice": {"label": "confirm_you_care"}}   # 连字符串都不是
    r = run("choice 不是字符串", [reply(judgment=bad)])
    assert r["answers"]["true_intent"]["choice"] == {"label": "confirm_you_care"}
    assert render_choice(r["answers"], "true_intent") == "暂未判断"   # 不炸，也不递给 setText

    bad = json.loads(json.dumps(JUDGMENT))
    bad["danger_level"] = {"score": 42, "probabilities": {"9": 1}}
    r = run("分数超范围", [reply(judgment=bad)])
    assert r["answers"]["danger_level"]["score"] == 42               # 不夹回上限
    assert render_tension(42) == "紧张度待判断"   # 上游给 11 分也是这个效果；夹成 9 就成了「9/9」

    bad = json.loads(json.dumps(JUDGMENT))
    bad["danger_level"] = {"score": "很高"}
    r = run("分数不是数字", [reply(judgment=bad)])
    assert r["answers"]["danger_level"]["score"] == "很高"           # 不丢
    assert render_tension("很高") == "紧张度待判断"

    bad = json.loads(json.dumps(JUDGMENT))
    bad["danger_level"] = {"score": float("inf")}                    # JSON 里的 Infinity
    r = run("分数是 Infinity", [reply(judgment=bad)])
    assert r["answers"]["danger_level"]["score"] == float("inf")
    assert render_tension(float("inf")) == "紧张度待判断"             # isfinite 那一关拦下

    body = json.loads(reply())
    body.pop("best_reply"), body.pop("reply_scores")
    r = run("排序整块没给", [json.dumps(body, ensure_ascii=False)])
    assert r["best_index"] == 0 and r["scores"] == [0.0, 0.0, 0.0]
    assert "best_reply" not in r["answers"] and len(r["answers"]) == len(JUDGE_QUESTIONS)

    body = json.loads(reply())
    body.pop("judgment")
    r = run("判断整块没给", [json.dumps(body, ensure_ascii=False)])
    assert set(r["answers"]) == {"best_reply"} and r["best_index"] == 1

    r = run("只给了两条（追问补上第三条）", [
        reply(replies=REPLIES[:2], reply_scores={"reply_a": 0.3, "reply_b": 0.7}),
        json.dumps({"replies": ["我记着呢 你等我说完"],
                    "reply_scores": {"reply_a": 0.5}}, ensure_ascii=False)])
    assert r["candidates"][:2] == REPLIES[:2] and len(r["candidates"]) == 3
    # 两次调用的分凑在一起不成分布，照样原样递下去（补上来那条的分落在 reply_c 上）
    assert r["best_index"] == 1 and r["scores"] == [0.3, 0.7, 0.5]

    r = run("只给了两条且追问也炸了", [
        reply(replies=REPLIES[:2], reply_scores={"reply_a": 0.3, "reply_b": 0.7}),
        draft.JevError("network down")], expect_cands=2)
    assert r["candidates"] == REPLIES[:2] and r["best_index"] == 1

    # 截断 → 只有两条能用 → 会追问一次；追问再炸就带着手里这两条收工（跟上游一个脾气）
    r = run("截断成半个 JSON", ['{"replies": ["等我想想", "你别急 我这就说", "上周三你',
                            draft.JevError("timeout")], expect_cands=2)
    assert r["candidates"] == REPLIES[:2]          # 完整的两条抢出来了，半截那条丢掉
    assert r["answers"] == {} and r["scores"] == [0.0, 0.0, 0.0]

    r = run("整段都不是 JSON（退回上游逐行兜底）",
            ["1. 等我想想\n2. 你别急 我这就说\n3. 上周三你说的那家店对吧"])
    assert r["candidates"] == REPLIES and r["best_index"] == 0 and r["answers"] == {}

    r = run("判断摊在顶层没套 judgment", [json.dumps(
        {"replies": list(REPLIES), "best_reply": "reply_a",
         "reply_scores": {"reply_a": 0.5, "reply_b": 0.3, "reply_c": 0.2}, **JUDGMENT},
        ensure_ascii=False)])
    assert r["best_index"] == 0 and r["answers"]["she_needs"]["choice"] == "关注与在意"

    # 点名那条被出口过滤掉了（复读对方原话）：标签原样递下去，engine 自己退第一条，分还在
    r = run("点名的候选被过滤掉", [reply(
        replies=["那你说。", "我记着呢", "上周三那家店"], best_reply="reply_a",
        reply_scores={"reply_a": 0.6, "reply_b": 0.25, "reply_c": 0.15}),
        draft.JevError("timeout")], expect_cands=2)
    assert r["answers"]["best_reply"]["choice"] == "reply_a"   # 不丢：engine 本来就防着认不出的标签
    assert r["best_index"] == 0 and r["scores"] == [0.25, 0.15, 0.0]

    # 点了个根本不存在的下标（只有两条却点 reply_c）：同样原样递下去，engine 夹回第一条
    r = run("点名的下标不存在", [reply(
        replies=REPLIES[:2], best_reply="reply_c",
        reply_scores={"reply_a": 0.4, "reply_b": 0.6}),
        draft.JevError("timeout")], expect_cands=2)
    assert r["answers"]["best_reply"]["choice"] == "reply_c" and r["best_index"] == 0

    # 预览界面的那份合成结果（tools/preview_ui.py 的 _RESULT）也得是 overlay 读得懂的形状
    preview = literal("tools/preview_ui.py", "_RESULT")
    check_shape(preview, len(preview["candidates"]))
    assert preview["best_index"] == 1 and preview["answers"]["danger_level"]["score"] == 0.0
    assert render_choice(preview["answers"], "true_intent") == "轻松交流"
    print("\n  preview_ui._RESULT: overlay 要读的字段齐了，形状对得上")

    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
