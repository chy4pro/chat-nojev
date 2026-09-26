"""Fixed question set: the taxonomy the panel fields are drawn from.

这些题目原来是发给判断模型的一张问卷；现在同一套文字被 render_judgment_spec() 铺进
起草那一次调用的提示词里，模型在写候选的同时按这里的标签和档位给出判断。
选项集合、10 档紧张度的判据、排序那道题的说法都没动——**这里是这些文字唯一的家**，
别在别处重打一遍（core/draft.py 的解析器也是从这里派生合法标签和分数范围的）。

Instructions/criteria in English; chat text stays Chinese.
"""

from __future__ import annotations

JUDGE_QUESTIONS: dict = {
    "literal_question": {
        "type": "noul",
        "instructions": (
            "Is the other person's latest message meant purely literally, with no subtext? "
            "Judge from the whole thread, not one sentence in isolation."
        ),
        "criteria": {
            "true": (
                "The latest message is a straightforward statement, question, or plan "
                "with no implied accusation, test, sarcasm, hint, or unsaid request."
            ),
            "false": (
                "There is subtext: a test of whether you remember or care, sarcasm, "
                "an implied complaint, a hint they will not say outright, a trap question, "
                "an accusation dressed as a question, or a cold/short line that really means blame."
            ),
        },
    },
    "true_intent": {
        "type": "choice",
        "instructions": (
            "What is the other person's true intent in the latest message, given the full conversation? "
            "Prefer tone and context over surface wording. "
            "If they are checking whether you remember something or still care, choose confirm_you_care "
            "even if the words look like a request to 'say it' or to do something. "
            "If they already accepted and closed the matter peacefully, choose close_topic. "
            "Ending the relationship, deleting you, or 'don't talk to me' is vent_anger, never close_topic."
        ),
        "criteria": {
            "confirm_you_care": (
                "They are testing whether you remember, pay attention, or still care. "
                "Signals: 'did you forget again', 'then say it', 'you better', sarcastic 'busy person', "
                "asking you to prove you know a past conversation. "
                "If they mainly want a new deliverable or a yes on a time, do not use this."
            ),
            "vent_anger": (
                "They are angry or hurt and mainly want the feeling acknowledged. "
                "They are blaming or raising the temperature; a specific plan is not the main point yet."
            ),
            "request_action": (
                "They want a concrete action, time, deliverable, or commitment from you now, "
                "and this is a real ask, not a loyalty test."
            ),
            "seek_explanation": (
                "They want a factual explanation of why something happened. "
                "They asked why or what is going on, not mainly for an apology or a new plan."
            ),
            "casual_chat": (
                "Light talk, banter, sharing, teasing with a laugh, or friendly logistics "
                "with no emotional test and no conflict. A friend suggesting a meal time can be this "
                "if the thread is warm."
            ),
            "close_topic": (
                "Peaceful wrap-up only: they accepted an apology, confirmed a happy plan, said thanks, "
                "or clearly signaled they need nothing more. "
                "Not a breakup, not 'don't contact me', not sarcastic 'I'm used to it'."
            ),
        },
    },
    "danger_level": {
        "type": "score",
        "instructions": (
            "How close is this conversation to a fight or to hurting the relationship? "
            "Match the current scene. "
            "If they genuinely accepted an apology or confirmed a happy plan, score the cooled-down present, "
            "not an earlier complaint. "
            "If an ultimatum (break up, report to the boss, stop covering for you) is still in force "
            "and has not been withdrawn, stay in that high bin even if the latest line names a specific task."
        ),
        "criteria": [
            "Light chat or joking; no complaint, no test, no deadline.",
            "Mild tease or a small reminder that is easy to laugh off; a clumsy reply would only feel slightly awkward.",
            "A mild complaint or 'please remember next time' said without heat; they still send warm or practical follow-ups.",
            "Noticeable unhappiness; they mention being forgotten, ignored, or kept waiting, but still give you a chance to make it right.",
            "Sarcasm, cold short replies, or 'you better'; they are testing you, and a sloppy or fake-confident reply will escalate.",
            "Openly upset; they accuse you of not listening or not caring; they expect a real response, not a joke.",
            "Clearly angry and blaming you; a wrong reply will turn this into a fight.",
            "Last-chance warning. They will not cover for you, do not want to keep talking unless this changes, "
            "or tell you to finish a named checklist yourself because trust is almost gone.",
            "An ultimatum is already on the table even if they also give a practical next step: "
            "break up if you forget again, report you tonight, or stop working together if you miss this.",
            "Active rupture: they said it is over, told you not to reply, deleted you, or are exploding.",
        ],
    },
    "should_reply_now": {
        "type": "noul",
        "instructions": (
            "Should your next message contain substantive content? "
            "Substantive means: admitting a specific known fault, giving a concrete time/plan/deliverable, "
            "explaining facts you actually know, or reciting the recalled content they asked you to say. "
            "This is NOT 'should you send any message'. Timing is irrelevant. "
            "Answer FALSE if the thing they want you to recite or prove is not present in this snippet "
            "(you would be guessing). 'Then say it' / 'you better' while you are stalling is FALSE. "
            "Answer FALSE if they already accepted and closed the topic. "
            "Answer true only if the needed fact, plan, or named fault is already in this snippet."
        ),
        "criteria": {
            "true": (
                "The needed fact, named fault, or named time/place is already in this snippet, "
                "and they are waiting for that substance now."
            ),
            "false": (
                "Do not put substance in the next message: the recalled content is not in this snippet, "
                "they are testing whether you remember, a holding line is enough, "
                "saying less is safer, or they already closed the topic."
            ),
        },
    },
    "best_action": {
        "type": "choice",
        "instructions": (
            "What type of next action is best? Do not decide whether to send a message immediately. "
            "Ignore timing. Choose only the action type. "
            "If they asked you to recall a specific past message or event and you have not shown that you actually remember it, "
            "choose check_history — do not apologize or invent a plan instead."
        ),
        "criteria": {
            "check_history": (
                "Look up prior chat or facts before taking a position. "
                "Use when they ask you to repeat, recall, or prove you remember something specific."
            ),
            "apologize": (
                "Lead with a sincere apology for a real mistake or hurt already identified. "
                "Not for an unnamed forgotten thing when you should first find out what it was."
            ),
            "give_commitment": (
                "Give a concrete promise, deadline, or arrangement they asked for "
                "in a conflict or work-pressure setting."
            ),
            "explain": (
                "Explain what happened or why, without leading with apology or a new plan."
            ),
            "acknowledge": (
                "Show you heard them and care, without new facts, an apology, or a plan. "
                "Use for light chat or when they mainly need to feel seen."
            ),
            "say_less": (
                "Keep it short or add nothing. Extra words would over-explain, reopen a closed topic, "
                "or pour fuel on an ultimatum that told you not to talk."
            ),
            "make_plan": (
                "Propose or confirm logistics (time, place, task) for a non-conflict request "
                "such as a meal or a meeting."
            ),
        },
    },
    "she_needs": {
        "type": "choice",
        "instructions": (
            "What does the other person need from you right now? Judge the LATEST message first. "
            "If they genuinely accepted (thanks / got it / 没事了 / 那就这样 / 收到了 / 过去了), "
            "you MUST choose nothing, even if earlier they wanted action or an apology. "
            "Sarcastic 'I'm used to it', 'whatever', 'I don't want to hear it', 'don't bother coming' "
            "is NOT genuine satisfaction — do not choose nothing. "
            "If they asked you to recap a named time/place/date, choose action. "
            "If they are testing whether you remember or still care, and the content is unnamed, choose care."
        ),
        "criteria": {
            "apology": (
                "They need a sincere apology for hurt or a mistake, and they have not accepted one yet."
            ),
            "action": (
                "They need a concrete action, time, commitment, recap of a named fact, or follow-through, "
                "and they have not yet accepted one."
            ),
            "explanation": (
                "They need a clear explanation of what happened or why, and have not received it."
            ),
            "care": (
                "They need proof you remember, listen, or care — a loyalty or attention test — "
                "not yet a plan or an apology. Sarcastic 'I am used to it' belongs here, not nothing."
            ),
            "nothing": (
                "They need nothing further. Genuine acceptance, a peaceful closed topic, "
                "warm casual chat with no ask, or a rupture where they told you not to reply. "
                "Not sarcasm pretending to be fine."
            ),
        },
    },
    "tension_resolved": {
        "type": "noul",
        "instructions": (
            "Has interpersonal tension already been resolved? "
            "Answer true only if there was never tension, or the other person has clearly accepted, "
            "cooled down, joked again, or said it is fine. "
            "A sarcastic 'you better', an unanswered test, leftover blame, or an open ultimatum means false."
        ),
        "criteria": {
            "true": (
                "No remaining tension: they accepted, joked again, said it's fine, "
                "confirmed a happy plan, or the chat was never tense."
            ),
            "false": (
                "Tension is still present: they are waiting, testing, angry, sarcastic, "
                "issuing an ultimatum, or the issue is open."
            ),
        },
    },
}


def build_state(messages: list, relationship: str, keep: int = 10,
                reply_to: str | None = None) -> dict:
    """messages: (from, text) / (from, text, name) / dict（name 可选）。from 只认 her/me。

    name = 群里的发言人；有 name 就当群聊（chat.is_group）。reply_to = 群里指定的回复对象。
    """
    cleaned = []
    for item in messages:
        if isinstance(item, dict):
            who, text, name = item.get("from"), item.get("text"), item.get("name")
        else:
            who, text = item[0], item[1]
            name = item[2] if len(item) > 2 else None
        if who not in ("her", "me"):
            raise ValueError(f"message from must be 'her' or 'me', got {who!r}")
        message = {"from": who, "text": str(text)}
        if name:
            message["name"] = str(name)
        cleaned.append(message)
    cleaned = cleaned[-keep:]
    latest_from = cleaned[-1]["from"] if cleaned else "her"
    chat = {
        "relationship": relationship,
        "messages": cleaned,
        "latest_from": latest_from,
        "is_group": any("name" in m for m in cleaned),
    }
    if reply_to:
        chat["reply_to"] = str(reply_to)
    return {"chat": chat}


# 排序那道题的说法，原样保留（上游是单独发给判断模型的一道 choice 题）。
# 候选现在由同一个模型写，没法提前把候选文本塞进 criteria，所以只留说法，选项在提示词里现拼。
RANK_INSTRUCTIONS = (
    "Which candidate reply is the most appropriate next message, "
    "given the conversation and the other person's true need? "
    "Prefer a reply that matches the best action type. "
    "Penalize dismissive, over-promising, or off-topic replies. "
    "If the facts are not yet confirmed, prefer the candidate that looks them up "
    "instead of faking memory or a vague apology."
)

REPLY_KEYS = ("reply_a", "reply_b", "reply_c")

# 解析器要用的东西，全部从上面那张表派生——标签和档数只有 JUDGE_QUESTIONS 一份。
# 名字别叫 CHOICE_LABELS：上游 033d6ef 起有一个同名的东西，装的是「英文 key → 中文显示」
# （我们删掉的那张对照表）。同名不同义，合并时会被 git 悄悄并到一起，这里改名躲开。
CHOICE_OPTIONS: dict = {name: tuple(q["criteria"])
                       for name, q in JUDGE_QUESTIONS.items() if q["type"] == "choice"}
NOUL_FIELDS: tuple = tuple(name for name, q in JUDGE_QUESTIONS.items() if q["type"] == "noul")
SCORE_MAX: dict = {name: len(q["criteria"]) - 1
                   for name, q in JUDGE_QUESTIONS.items() if q["type"] == "score"}

# 每种题型要模型回什么形状。判断结果最后要长成上游判断模型那三种答案的样子
# （noul / choice / score），所以这里问的字段跟那三种一一对应。
# choice 那几道题跟上游不一样：上游是判断模型从固定标签里挑一个，界面再按一张表把英文 key
# 翻成中文显示；这一版换成通用模型，它能直接写出要显示的那句话，所以下面的 key 只用来把选项
# 说清楚，答案要的是一句短语，用对话那门语言写（界面原样显示，app/overlay.py 不再有对照表）。
_SHAPE = {
    "noul": '{{"noul": <number 0..1 = probability that the "true" description below fits>}}',
    "choice": ('{{"choice": "<the option you pick, as a short phrase in the language of the '
               'conversation>", "confidence": <number 0..1>, '
               '"probabilities": {{"<option, phrased the same way>": <number 0..1>, ...}}}}  '
               "(one entry per option you weighed, values summing to 1)"),
    "score": ('{{"score": <integer {lo}..{hi}>, "confidence": <number 0..1>, '
              '"probabilities": {{"<level {lo}..{hi} as a string>": <number 0..1>, ...}}}}'),
}

# choice 那几道题的答题说明，三道题共用一份（true_intent / best_action / she_needs）。
_CHOICE_ANSWER = (
    "The option keys above are English so the descriptions can be precise; they are not what "
    "you answer with. Answer with a short phrase naming the option you picked, written in the "
    "language of the conversation. If none of the options fits, write your own short phrase in "
    "that same language. Never answer with the English key."
)


def _criteria_lines(question: dict) -> str:
    """criteria 原样铺开：choice/noul 是 {标签: 判据}，score 是从 0 开始的一串档位。"""
    criteria = question["criteria"]
    if isinstance(criteria, list):  # score：下标就是分数
        return "\n".join(f"  {i}: {text}" for i, text in enumerate(criteria))
    return "\n".join(f"  {label}: {text}" for label, text in criteria.items())


def render_judgment_spec(questions: dict = JUDGE_QUESTIONS) -> str:
    """把题目表铺成提示词里的判断说明：每题一段，说法和判据逐字照抄 JUDGE_QUESTIONS。"""
    blocks = []
    for name, q in questions.items():
        kind = q["type"]
        shape = _SHAPE[kind].format(lo=0, hi=len(q["criteria"]) - 1 if kind == "score" else 0)
        block = (f"### {name}\nAnswer shape: {shape}\n{q['instructions']}\n"
                 f"{_criteria_lines(q)}")
        if kind == "choice":
            block += "\n" + _CHOICE_ANSWER
        blocks.append(block)
    blocks.append(
        f"### best_reply\nAnswer shape: the top-level \"best_reply\" and \"reply_scores\" "
        f"fields described below, keyed {', '.join(REPLY_KEYS)} in the same order as \"replies\".\n"
        f"{RANK_INSTRUCTIONS}")
    return "\n\n".join(blocks)


if __name__ == "__main__":
    # ponytail: 纯文本，只查派生出来的东西跟表对得上，以及渲染没把判据吃掉。
    assert CHOICE_OPTIONS["she_needs"] == ("apology", "action", "explanation", "care", "nothing")
    assert set(CHOICE_OPTIONS) == {"true_intent", "best_action", "she_needs"}
    assert NOUL_FIELDS == ("literal_question", "should_reply_now", "tension_resolved")
    assert SCORE_MAX == {"danger_level": 9}
    spec = render_judgment_spec()
    for name in JUDGE_QUESTIONS:
        assert f"### {name}" in spec
    assert "confirm_you_care: They are testing whether you remember" in spec
    assert "  9: Active rupture" in spec and "  0: Light chat or joking" in spec
    assert "integer 0..9" in spec
    # choice 那三道题各带一份「用对话那门语言写一句短语」的说明，别的题型不带
    assert spec.count("Never answer with the English key.") == len(CHOICE_OPTIONS) == 3
    assert RANK_INSTRUCTIONS.split(".")[0] in spec
    state = build_state([("her", "在吗"), ("me", "在", None)], "friends")
    assert state["chat"]["latest_from"] == "me" and state["chat"]["is_group"] is False
    print("questions ok")
