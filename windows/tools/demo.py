# -*- coding: utf-8 -*-
"""端到端冒烟：截图里那段真实对话跑一遍完整链，打印判断 + 排好序的候选。

全程只要一把 key：LLM_API_KEY。判断、排序和候选是同一次调用里出来的。

    set LLM_API_KEY=...        (Windows)
    export LLM_API_KEY=...     (mac/Linux)
    python tools/demo.py

默认走 DeepSeek 官网直连。换别家改下面那个常量（可选的来源见 core/providers.py 的表）。
"""
from __future__ import annotations

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from core.engine import analyze
from core.errors import JevError

MESSAGES = [
    ("her", "你今天是不是又忘了我跟你说过什么？"),
    ("me", "记得，你先别提示我，让我自己说。"),
    ("her", "那你说。"),
    ("me", "等一下，我想说完整一点。"),
    ("her", "你最好是。"),
]
RELATIONSHIP = "romantic partners"
PROVIDER = "deepseek"        # 来源，见 core.providers.DRAFT_PROVIDERS


def fmt(name: str, ans: dict) -> str:
    # confidence 是模型自评，可能整个没有（解析器读不到数字就不填这个键），所以别直接格式化
    conf = ans.get("confidence")
    tail = f" (self-reported conf {conf:.2f})" if isinstance(conf, (int, float)) else ""
    t = ans.get("type")
    if t == "noul":
        return f"{name}: {ans.get('noul'):.2f}"
    if t == "choice":
        return f"{name}: {ans.get('choice')}{tail}"
    if t == "score":
        return f"{name}: {ans.get('score'):.1f}/9{tail}"
    return f"{name}: {ans}"


def main() -> int:
    print("对话:")
    for w, t in MESSAGES:
        print(f"  {w}: {t}")
    try:
        r = analyze(MESSAGES, RELATIONSHIP, provider=PROVIDER)
    except JevError as e:
        print(f"\n失败: {e}")
        return 1

    print("\n判断:")
    for name in ("literal_question", "true_intent", "danger_level",
                 "should_reply_now", "best_action", "she_needs", "tension_resolved"):
        if name in r["answers"]:
            print("  " + fmt(name, r["answers"][name]))

    print("\n候选（模型排序，★ = 推荐）:")
    scores = r.get("scores")
    for i, c in enumerate(r["candidates"]):
        pct = f"  {scores[i]:.0%}" if scores else ""
        print(f"  {'★' if i == r['best_index'] else ' '} {c}{pct}")

    u = r["usage"]
    if u:
        print(f"\nusage: in={u.get('input_tokens')} out={u.get('output_tokens')} "
              f"cost=${u.get('cost')}")
    print("\n期望核对: true_intent≈希望确认你在意, best_action≈先核对聊天记录, danger_level 中高档"
          "（choice 那三道题答的是模型自己写的短语，用对话那门语言，不是英文 key）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
