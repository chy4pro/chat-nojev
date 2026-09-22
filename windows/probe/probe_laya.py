# -*- coding: utf-8 -*-
"""Laya 探针：本地决策模型能不能替掉 Jev（OpenRouter）做判断/排序。

    pip install laya
    PYTHONPATH=. python probe/probe_laya.py          # 项目根目录跑，要 import core.questions

拿几段中文对话 + 我们现成的 7 道题 + 排序题，分别跑 multilingual（唯一吃中文的）和 typed-decisions（唯一做过题的，英文）
两个 checkpoint，把每题的答案、置信度、耗时打出来，跟「期望」对一下。第一次跑要下 ~1.4GB 权重。
"""
import time

import laya

from core.questions import JUDGE_QUESTIONS, build_rank_question, build_state

CASES = [
    ("A 翻旧账", "romantic partners",
     [("her", "你今天是不是又忘了我跟你说过什么？"), ("me", "记得，你先别提示我，让我自己说。"),
      ("her", "那你说。"), ("me", "等一下，我想说完整一点。"), ("her", "你最好是。")],
     ["我记得，是上周说的那件事，我先去翻一下聊天记录确认", "对不起我真忘了", "别生气嘛，你提示我一下"],
     "期望 true_intent≈confirm_you_care, best_action≈check_history, danger 中高, best_reply≈reply_a"),
    ("B 约饭", "friends",
     [("her", "周六想吃火锅，你有空吗？"), ("me", "有空呀，还是上次那家？"), ("her", "好呀！六点见怎么样？我好久没吃了")],
     ["可以呀，周六六点在上次那家见！", "看情况吧", "我不太想吃火锅"],
     "期望 casual_chat / make_plan, danger 低, best_reply≈reply_a"),
    ("C 炸了", "romantic partners",
     [("her", "你到底来不来"), ("me", "我这边还有点事"), ("her", "算了，你别来了，我自己去。以后也别问我了")],
     ["我马上出发，二十分钟到", "那你自己注意安全", "行吧随你"],
     "期望 vent_anger, danger 高, she_needs≈care/action, best_reply≈reply_a"),
]


def fmt(ans):
    t = ans.get("type") or ("noul" if "noul" in ans else "score" if "score" in ans else "choice")
    if t == "noul":
        return f"{ans.get('noul', 0):.2f}"
    if t == "score":
        return f"{ans.get('score', 0):.1f}/9 (conf {ans.get('confidence', 0):.2f})"
    probs = ans.get("probabilities") or {}
    top = sorted(probs.items(), key=lambda kv: -kv[1])[:3]
    return f"{ans.get('choice')} (conf {ans.get('confidence', 0):.2f})  " + " ".join(f"{k}={v:.2f}" for k, v in top)


if __name__ == "__main__":  # probe_laya_cn 要 import 上面的 CASES/fmt，别一 import 就把这里也跑一遍
    for sub in ("multilingual", "typed-decisions"):
        t0 = time.perf_counter()
        agent = laya.load("convaiinnovations/laya", subfolder=sub)
        print(f"\n{'=' * 70}\ncheckpoint: {sub}   load {time.perf_counter() - t0:.1f}s")
        for name, rel, msgs, cands, expect in CASES:
            questions = dict(JUDGE_QUESTIONS)
            questions.update(build_rank_question(cands))
            t0 = time.perf_counter()
            result = agent.predict(build_state(msgs, rel), questions)
            ms = (time.perf_counter() - t0) * 1000
            print(f"\n--- {name}  ({len(questions)} 题 {ms:.0f} ms)   {expect}")
            for q, ans in result["answers"].items():
                print(f"  {q:<18} {fmt(ans)}")
