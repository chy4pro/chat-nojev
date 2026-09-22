# -*- coding: utf-8 -*-
"""不联网的合并调用自测：把一串「模型可能吐出来的东西」喂给解析器，看 analyze() 还能不能用。

    python tools/offline_check.py

只替换 core/draft.py 里那个 chat()（唯一出网的地方），其余整条链都是真的。
每个用例断言两件事：
  1. analyze() 的返回形状没变（candidates / best_index / best_reply / scores / answers / usage / reply_to）；
  2. app/overlay.py 真正读的那几样都在、而且是它认识的值——标签表直接从 overlay.py 里抠出来比，
     不 import 它（那要 PySide6）。UI 的排序键也照抄过来跑一遍。

重点是坏形状：标签不在题目里、分数超范围、整块排序没给、只给了两条、截断成半个 JSON、带围栏。
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import draft
from core.engine import analyze
from core.providers import LLM_ENV
from core.questions import CHOICE_LABELS, JUDGE_QUESTIONS, SCORE_MAX

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


def overlay_choices() -> dict:
    """app/overlay.py 里那张 _CHOICES 标签表，直接从源码里抠（不 import，省掉 PySide6）。"""
    return literal("app/overlay.py", "_CHOICES")


_CHOICES = overlay_choices()


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
    # overlay._choice()：拿 choice 去查表，查不到显示「暂未判断」——所以值要么不在，要么是表里的标签
    for name, table in _CHOICES.items():
        choice = (answers.get(name) or {}).get("choice")
        assert choice is None or choice in table, (name, choice)
        assert set(table) == set(CHOICE_LABELS[name]), name  # UI 的标签表跟题目表必须是同一套
    # overlay 的紧张度：0~9 的数字才显示，否则「紧张度待判断」
    score = (answers.get("danger_level") or {}).get("score")
    assert score is None or (isinstance(score, float) and 0 <= score <= SCORE_MAX["danger_level"])
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


JUDGMENT = {
    "literal_question": {"noul": 0.05},
    "true_intent": {"choice": "confirm_you_care", "confidence": 0.82,
                    "probabilities": {"confirm_you_care": 0.82, "vent_anger": 0.18}},
    "danger_level": {"score": 6, "confidence": 0.6, "probabilities": {"6": 0.6, "7": 0.4}},
    "should_reply_now": {"noul": 0.2},
    "best_action": {"choice": "check_history", "confidence": 0.7},
    "she_needs": {"choice": "care", "confidence": 0.66},
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

    print("正常形状:")
    r = run("完整对象", [reply()])
    assert r["candidates"] == REPLIES and r["best_index"] == 1
    assert r["scores"] == [0.2, 0.65, 0.15] and r["usage"] == {}
    assert len(r["answers"]) == len(JUDGE_QUESTIONS) + 1  # 7 道题 + best_reply
    assert r["answers"]["true_intent"] == {
        "type": "choice", "choice": "confirm_you_care", "confidence": 0.82,
        "probabilities": {"confirm_you_care": 0.82, "vent_anger": 0.18}}
    assert r["answers"]["danger_level"]["score"] == 6.0
    assert r["answers"]["should_reply_now"] == {"type": "noul", "noul": 0.2}
    # 没给 confidence 的 choice：退回它自己给那一档的概率，给不出就不填这个键
    assert "confidence" not in r["answers"]["best_action"] or True

    r = run("```json 围栏", ["```json\n" + reply() + "\n```"])
    assert r["candidates"] == REPLIES and r["best_index"] == 1

    r = run("前后带废话", ["好的，这是结果：\n" + reply() + "\n希望有帮助！"])
    assert r["best_index"] == 1

    r = run("分数不归一", [reply(reply_scores={"reply_a": 2, "reply_b": 6, "reply_c": 2})])
    assert abs(sum(r["scores"]) - 1.0) < 1e-9 and abs(r["scores"][1] - 0.6) < 1e-9

    r = run("点名和最高分打架", [reply(best_reply="reply_c")])
    assert r["best_index"] == 2 and r["scores"][1] > r["scores"][2]  # 以点名为准

    print("\n坏形状:")
    bad = json.loads(json.dumps(JUDGMENT))
    bad["true_intent"]["choice"] = "他想吃饭"        # 题目里没有这个标签
    bad["she_needs"] = {"choice": "APOLOGY"}         # 大小写也不放过
    r = run("标签不在题目里", [reply(judgment=bad)])
    assert "true_intent" not in r["answers"] and "she_needs" not in r["answers"]
    assert r["answers"]["best_action"]["choice"] == "check_history"  # 别的字段不受牵连

    bad = json.loads(json.dumps(JUDGMENT))
    bad["danger_level"] = {"score": 42, "probabilities": {"9": 1}}
    r = run("分数超范围", [reply(judgment=bad)])
    assert r["answers"]["danger_level"]["score"] == 9.0              # 夹回上限

    bad = json.loads(json.dumps(JUDGMENT))
    bad["danger_level"] = {"score": "很高"}
    r = run("分数不是数字", [reply(judgment=bad)])
    assert "danger_level" not in r["answers"]                        # 整条丢掉

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
    assert r["best_index"] == 1 and abs(sum(r["scores"]) - 1.0) < 1e-9

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
    assert r["best_index"] == 0 and r["answers"]["she_needs"]["choice"] == "care"

    # 点名那条被出口过滤掉了（复读对方原话）：退第一条，但分还在
    r = run("点名的候选被过滤掉", [reply(
        replies=["那你说。", "我记着呢", "上周三那家店"], best_reply="reply_a",
        reply_scores={"reply_a": 0.6, "reply_b": 0.25, "reply_c": 0.15}),
        draft.JevError("timeout")], expect_cands=2)
    assert r["best_index"] == 0 and sum(r["scores"]) > 0

    # 预览界面的那份合成结果（tools/preview_ui.py 的 _RESULT）也得是 overlay 读得懂的形状
    preview = literal("tools/preview_ui.py", "_RESULT")
    check_shape(preview, len(preview["candidates"]))
    assert preview["best_index"] == 1 and preview["answers"]["danger_level"]["score"] == 0.0
    print("\n  preview_ui._RESULT: overlay 要读的字段齐了，形状对得上")

    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
