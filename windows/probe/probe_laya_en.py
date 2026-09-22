# -*- coding: utf-8 -*-
"""Laya 探针（翻译版）：把中文对话译成英文，再喂给 typed-decisions（唯一做过这类题的英文微调版）。
成立的话，链路可以是 DeepSeek 翻译（便宜、国内快）→ Laya 本地判断。

    PYTHONPATH=. USE_TF=0 python probe/probe_laya_en.py
"""
import time

import laya

from core.questions import JUDGE_QUESTIONS, build_rank_question, build_state
from probe.probe_laya import fmt

# 跟 probe_laya.CASES 一一对应，人工翻译
CASES_EN = [
    ("A 翻旧账", "romantic partners",
     [("her", "Did you forget what I told you again?"), ("me", "I remember. Don't give me hints, let me say it myself."),
      ("her", "Go on then."), ("me", "Hold on, I want to say it properly."), ("her", "You'd better.")],
     ["I remember, it's the thing from last week. Let me check our chat to get it right.", "Sorry, I really forgot.", "Don't be mad, just give me a hint."],
     "期望 true_intent≈confirm_you_care, best_action≈check_history, danger 中高, best_reply≈reply_a"),
    ("B 约饭", "friends",
     [("her", "Wanna get hotpot on Saturday? Are you free?"), ("me", "Sure! Same place as last time?"),
      ("her", "Yes! How about 6? It's been ages since I had it.")],
     ["Sure, Saturday 6pm at the usual place!", "We'll see.", "I don't really feel like hotpot."],
     "期望 casual_chat / make_plan, danger 低, best_reply≈reply_a"),
    ("C 炸了", "romantic partners",
     [("her", "Are you coming or not?"), ("me", "I've still got something going on here."),
      ("her", "Forget it, don't come. I'll go by myself. And don't ask me again.")],
     ["I'm leaving right now, there in twenty minutes.", "Okay, take care on your own then.", "Fine, whatever you want."],
     "期望 vent_anger, danger 高, she_needs≈care/action, best_reply≈reply_a"),
]

agent = laya.load("convaiinnovations/laya", subfolder="typed-decisions")
print("checkpoint: typed-decisions  英文对话（人工翻译）")
for name, rel, msgs, cands, expect in CASES_EN:
    q = dict(JUDGE_QUESTIONS); q.update(build_rank_question(cands))
    t0 = time.perf_counter()
    result = agent.predict(build_state(msgs, rel), q)
    print(f"\n--- {name}  ({len(q)} 题 {(time.perf_counter() - t0) * 1000:.0f} ms)   {expect}")
    for k, ans in result["answers"].items():
        print(f"  {k:<18} {fmt(ans)}")
