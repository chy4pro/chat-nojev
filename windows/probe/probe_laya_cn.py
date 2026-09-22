# -*- coding: utf-8 -*-
"""Laya 探针（中文题版）：multilingual checkpoint 用中文 instructions/criteria 问，跟英文题版对比。

    PYTHONPATH=. USE_TF=0 python probe/probe_laya_cn.py
"""
import time

import laya

from core.questions import build_state
from probe.probe_laya import CASES, fmt

CN = {
    "literal_question": {"type": "noul",
        "instructions": "对方最新这句话是不是纯字面意思、没有弦外之音？结合整段对话判断。",
        "criteria": {"true": "就是平铺直叙的陈述、提问或安排，没有暗示、试探、讽刺、没说出口的要求。",
                     "false": "有潜台词：在试探你记不记得/在不在乎、讽刺、暗含抱怨、不明说的暗示、设套的问题、冷淡短句其实是在怪你。"}},
    "true_intent": {"type": "choice",
        "instructions": "结合整段对话，对方最新一句的真实意图是什么？看语气和上下文，不看字面。如果是在确认你记不记得或在不在乎，选 confirm_you_care；已经平和收尾选 close_topic；分手、拉黑、「别再找我」是 vent_anger，绝不是 close_topic。",
        "criteria": {"confirm_you_care": "在试探你记不记得、上不上心、还在不在乎。典型：「又忘了？」「那你说」「你最好是」、阴阳怪气的「大忙人」、让你证明记得之前说过的话。",
                     "vent_anger": "在生气或受伤，主要想让情绪被看见；在指责、在升温，具体安排不是重点。",
                     "request_action": "要你现在给一个具体行动、时间、交付或承诺，是真的要，不是考验。",
                     "seek_explanation": "想要一个事实上的解释——问为什么、怎么回事，不是主要要道歉或新安排。",
                     "casual_chat": "闲聊、开玩笑、分享、带笑的调侃、友好的日常安排，没有情绪试探和冲突。",
                     "close_topic": "平和收尾：接受了道歉、确认了开心的安排、说了谢谢、明确表示不需要更多了。不是分手，不是「别联系我」，不是讽刺的「习惯了」。"}},
    "danger_level": {"type": "score",
        "instructions": "这段对话离吵架或伤害关系有多近？按当前场景打分。如果对方已经真心接受道歉或确认了愉快的安排，按缓和后的现状打分；如果最后通牒（分手、告状、不再帮你）还没撤回，即使最新一句在说具体事情也要停在高档。",
        "criteria": ["轻松闲聊或开玩笑，没有抱怨、试探、期限。",
                     "轻微调侃或小提醒，一笑而过；回得笨拙也只是稍微尴尬。",
                     "温和的抱怨或「下次记得」，没有火气，还在发温暖或务实的后续。",
                     "明显不高兴，提到被忘记、被忽视、被晾着，但还给你机会补救。",
                     "讽刺、冷淡短句、「你最好是」；在考验你，敷衍或装自信会升级。",
                     "直接指责，情绪明显，要一个态度。",
                     "已经在说「算了」「随便你」，关系在往下走。",
                     "最后通牒：不来就分手 / 告诉领导 / 以后别找我。",
                     "已经在执行断绝：拉黑、删除、「别再联系我」。"]},
    "should_reply_now": {"type": "noul",
        "instructions": "现在该马上回具体内容吗？还是应该先核对事实/先安抚再说？",
        "criteria": {"true": "对方等着一个直接的回答或安排，现在回具体内容是对的。",
                     "false": "先别急着给具体内容：要先翻记录确认、先承认情绪、或者对方根本不需要回。"}},
    "best_action": {"type": "choice",
        "instructions": "接下来最合适的动作是什么？",
        "criteria": {"check_history": "先去核对聊天记录/事实再回，别装记得。",
                     "apologize": "为已知的问题诚恳道歉。",
                     "give_commitment": "给出具体承诺（时间、做什么）。",
                     "explain": "说明事实和原因。",
                     "acknowledge": "回应并表达理解对方的感受。",
                     "say_less": "简短回应或留白，别说多。",
                     "make_plan": "商量具体安排。"}},
    "she_needs": {"type": "choice",
        "instructions": "对方现在最需要的是什么？",
        "criteria": {"apology": "真诚道歉。", "action": "具体行动或安排。", "explanation": "清楚的解释。",
                     "care": "被关注、被在意。", "nothing": "可能不需要补充回应了。"}},
    "tension_resolved": {"type": "noul",
        "instructions": "这段对话里的紧张已经化解了吗？",
        "criteria": {"true": "对方已经接受、放下、语气回暖。", "false": "还没化解，或者根本没紧张过之外的情况按未化解。"}},
}


def rank_cn(cands):
    return {"best_reply": {"type": "choice",
        "instructions": "结合对话和对方的真实需要，哪条候选回复最合适？优先跟 best_action 一致的；敷衍、过度承诺、跑题的扣分；事实没确认时优先去核对的那条，而不是装记得或空泛道歉。",
        "criteria": dict(zip(("reply_a", "reply_b", "reply_c"), cands))}}


agent = laya.load("convaiinnovations/laya", subfolder="multilingual")
print("checkpoint: multilingual  中文题")
for name, rel, msgs, cands, expect in CASES:
    q = dict(CN); q.update(rank_cn(cands))
    t0 = time.perf_counter()
    result = agent.predict(build_state(msgs, rel), q)
    print(f"\n--- {name}  ({len(q)} 题 {(time.perf_counter() - t0) * 1000:.0f} ms)   {expect}")
    for k, ans in result["answers"].items():
        print(f"  {k:<18} {fmt(ans)}")
