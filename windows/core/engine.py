# -*- coding: utf-8 -*-
"""整条链的唯一入口：对话 → 一次调用拿到 3 条候选 + 判断 + 排序 → 结构化结果。

平台无关。SSE 消费者、悬浮窗、命令行 demo 都只调 analyze()。
上游是起草一次、判断模型再一次；这一版合成一次，产出方换了，下面的处理一个字没动。
"""
from __future__ import annotations

try:
    from .draft import draft_and_judge
    from .errors import JevError
except ImportError:
    from draft import draft_and_judge
    from errors import JevError

_REPLY_IDX = {"reply_a": 0, "reply_b": 1, "reply_c": 2}


def analyze(messages: list, relationship: str, model: str | None = None,
            timeout: float = 30, context: int = 10, provider: str = "deepseek",
            base_url: str | None = None, reply_to: str | None = None, style: str = "",
            thinking: bool = False) -> dict:
    """messages: [(from, text)] from ∈ {her, me}，最新一条在最后；
    群聊里可以带第三项 name（说这句话的人），单聊不带。
    context: 起草和判断看最近多少条消息（用户设置里的「参考上下文」）。
    provider: 走哪家（core.providers.DRAFT_PROVIDERS），base_url 只有自定义来源要传。
    reply_to: 群聊里指定回复给谁；None = 正常回复。
    style: 用户自己描述的说话风格，只影响起草。
    thinking: 是否开思考模式，默认关。
    model = None 用该来源的默认模型。

    返回 {candidates, best_index, best_reply, scores, answers, usage, reply_to}。
    scores 是每条候选的胜出概率（0~1），取自 best_reply.probabilities，取不到记 0.0。
    只有对方最新说话时才有意义调它——是不是该触发由调用方判断（看 latest_from）。
    """
    result = draft_and_judge(messages, relationship, provider=provider, model=model,
                             base_url=base_url, timeout=timeout, keep=context,
                             reply_to=reply_to, style=style, thinking=thinking)
    candidates = result["candidates"]
    if not candidates:  # 注入过滤可以把起草结果全扔掉；接着取 [0] 会 IndexError
        raise JevError("起草结果没有可用候选回复")

    answers = result.get("answers") or {}
    best_key = (answers.get("best_reply") or {}).get("choice")
    best_index = _REPLY_IDX.get(best_key, 0)  # 解析不出就退第一条
    if best_index >= len(candidates):
        best_index = 0

    probabilities = (answers.get("best_reply") or {}).get("probabilities") or {}
    scores = [0.0, 0.0, 0.0]
    for key, idx in _REPLY_IDX.items():
        try:
            scores[idx] = float(probabilities.get(key, 0.0))
        except (TypeError, ValueError):
            scores[idx] = 0.0  # 脏数据一律按 0 处理

    return {
        "candidates": candidates,
        "best_index": best_index,
        "best_reply": candidates[best_index],
        "scores": scores,
        "answers": answers,
        "usage": result.get("usage") or {},
        "reply_to": reply_to,
    }


if __name__ == "__main__":
    # 候选被过滤光时要抛 JevError，不能在取第一条时 IndexError。
    from unittest.mock import patch

    with patch("__main__.draft_and_judge",
               return_value={"candidates": [], "answers": {}, "usage": {}}):
        try:
            analyze([("her", "hello")], "friends")
            raise SystemExit("应当抛错")
        except JevError as e:
            assert "没有可用候选" in str(e)
    print("engine ok")
