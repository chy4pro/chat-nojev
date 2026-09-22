"""判断题目：意图 + 风险的那张表，以及把它铺进生成提示词的渲染。

这是上游 `src/judge.py` 里那份数据的新家。**文字一个字没改**：意图的 8 个标签和描述、
风险的 10 档判据、每种意图对应的「具体行动」都从上游原样搬过来——换掉的只是产出方。

上游有两个判断模型：本地 `Mapika/decider-2b`（`judge.Judge`，一次前向读 A/B/C… 字母
logits 出两个分布）和云端 TypeSafe Jev（`judge_jev.JevJudge`，POST /v1/systemone）。
这一版把判断合进生成那一次调用里，所以两个都没了：

  删掉的文件      src/judge.py（本地 decider-2b + FallbackJudge + make_judge + 内存守卫）
                  src/judge_jev.py（TypeSafe Jev 客户端）
                  src/judge_zh_test.py（decider-2b 的 22 条中文回归，测的是已删掉的模型）
  删掉的配置      TYPESAFE_API_KEY / TYPESAFE_BASE_URL / TYPESAFE_MODEL
  一起没了的能力  **离线判断**。上游不配任何 key 也能出意图和风险（本地模型，不联网、
                  不花钱），现在判断跟着生成一起出网。要想要回来：把 judge.Judge 整个
                  留着，在 hud 里加一条「本地判断」开关，判断走它、生成走 API——那就是
                  上游的两次调用，这个项目的前提正是不要那第二次。详见 docs/EQUIVALENCE.md。

题目文字只有这一份，别在别处重打（src/generate.py 的提示词从这里渲染）。
"""

from __future__ import annotations

# 描述保持这个长度是有实测依据的，别为了省 prefill 时间去瘦身：两轮压缩措辞
# （保语义锚点、每条砍 ~1/3 字符）在 22 条回归上分别是 81.8% 和 77.3%，都低于
# 原文的 86.4%——批评/要解释 的边界对措辞极敏感。省下的 ~100 ms 判断又藏在
# 停稳窗口里基本不可见，不划算（2026-09 实测，judge_zh_test.py 已改为直接
# import 这份 INTENTS，改这里必须重跑回归）。
#
# ↑ 上游原注释，原样留着。口径要更新一句：那组数字是在 decider-2b 上测的，
# 那个模型这一版已经不在了（judge_zh_test.py 一并删了），所以这些数字现在只是
# 出处说明，不再是改这段文字的门槛。文字本身照旧不动——它现在是提示词的一部分，
# 通用模型同样按这几句描述去分意图。
INTENTS = {
    "派活": "对方要我做一件事或接一个任务",
    "催进度": "对方在催促我尽快完成某个已在办的事",
    "问进度": "对方在询问某件事的进展或状态",
    "批评": "对方对我的工作或结果表达不满、指出错误",
    "要解释": "对方要求我说明原因或给出解释",
    "闲聊": "对方只是在聊天、分享或表达感受，没有具体要求",
    "约会议": "对方想安排一次会议或通话",
    "夸奖": "对方在肯定、称赞我的成果",
}

RISK_LEVELS = [
    "完全没风险，怎么回都行",
    "基本没风险",
    "平淡，正常回就好",
    "需要稍微留神",
    "有点敏感，措辞注意",
    "需要谨慎，可能被挑刺",
    "比较危险，容易得罪人或踩坑",
    "很危险，说错要出问题",
    "非常危险，涉及责任或利益",
    "极度危险，先别回，想清楚再说",
]

# 上游：`# V0: actions are a static derivation, no generation involved` —— 判断模型
# 不写字，所以「具体行动」只能按意图查这张表。现在写候选的模型顺手也把这三条写了，
# 表不再用来查（意图是模型自己写的短语，查也查不到），改当**示例**铺进提示词：
# 上游这几句的口吻和颗粒度就是我们要的，原样留着当样例最省事，也最忠实。
ACTION_MAP = {
    "派活": ["接住", "问清交付标准和期限", "先给个时间点"],
    "催进度": ["先给当前状态", "给明确的完成时间", "别解释太多"],
    "问进度": ["直接说事实", "给下个节点", "有卡点就说卡点"],
    "批评": ["先认下来", "别急着辩解", "给补救方案"],
    "要解释": ["说清原因", "别找借口", "给改进措施"],
    "闲聊": ["轻松回应", "可以互动", "不用当真"],
    "约会议": ["确认时间", "说清议程", "准备好材料"],
    "夸奖": ["接住并感谢", "别过度谦虚", "可以顺带提下一步"],
}

# 两道题的题干，逐字来自上游 judge_jev.JevJudge.judge 发出去的 questions（本地
# judge.Judge 的 prompt 用的是同样两句）。
INTENT_QUESTION = "这句话的真实意图是什么？"
RISK_QUESTION = "如果直接回复这句话，风险有多大？"
# 排序那道题，逐字来自上游 rank_candidates。上游是把候选当选项单独问一次判断模型；
# 这一版每条候选是同一次调用自己写的，所以这句话改在同一个提示词里问。
RANK_QUESTION = "哪一条回复最合适？"
ACTIONS_QUESTION = "收到这条消息，接下来具体该怎么做？"

# 意图那道题跟上游不一样的地方：上游是判断模型从上面 8 个固定标签里挑一个（分类器
# 只能在闭集里选），面板直接显示那个标签。这一版是通用模型，它能直接写出要显示的那
# 句话，所以标签只用来把「意图」这个概念说清楚，答案要的是一句短语，用对话那门语言写，
# 界面原样显示。
_INTENT_ANSWER = (
    "上面 8 个名字是把「意图」这件事说清楚用的，不是答案的取值范围。"
    "答一句短语，说你判断的意图是什么，用对话那门语言写；"
    "上面哪个贴切就写哪个，都不贴切就自己写一句短的。"
)


def render_judgment_spec() -> str:
    """判断说明：铺进生成提示词的那一段。题干和判据逐字来自上面那张表。"""
    intents = "\n".join(f"  {name}: {desc}" for name, desc in INTENTS.items())
    risks = "\n".join(f"  {i}: {text}" for i, text in enumerate(RISK_LEVELS))
    examples = "；".join(f"{name} → {'、'.join(acts)}"
                         for name, acts in list(ACTION_MAP.items())[:3])
    return (
        f"### intent\n答案形状：\"intent\": \"<一句短语>\", \"confidence\": <0~1 的数>, "
        f"\"intent_probs\": {{\"<意图，写法跟上面一致>\": <0~1 的数>, ...}}\n"
        f"{INTENT_QUESTION}\n{intents}\n{_INTENT_ANSWER}\n\n"
        f"### risk\n答案形状：\"risk\": <0..{len(RISK_LEVELS) - 1} 的整数>, "
        f"\"risk_probs\": {{\"<档位，0..{len(RISK_LEVELS) - 1} 的字符串>\": <0~1 的数>, ...}}\n"
        f"{RISK_QUESTION}\n{risks}\n\n"
        f"### actions\n答案形状：\"actions\": [\"<短句>\", ...]（最多 3 条）\n"
        f"{ACTIONS_QUESTION}每条不超过 10 个字，是给我看的提示，不是要发出去的话。"
        f"用对话那门语言写。示例（上游按意图给的口径）：{examples}\n\n"
        f"### reply_scores\n答案形状：下面说的那个 \"reply_scores\" 数组。\n"
        f"{RANK_QUESTION}给自己写的每条候选一个 0~1 的分，越大表示越该发出去。"
    )


if __name__ == "__main__":
    # 纯数据，只查几条不变式：档数、题干有没有被改写、渲染没把判据吃掉。
    assert len(INTENTS) == 8 and len(RISK_LEVELS) == 10
    assert set(ACTION_MAP) == set(INTENTS)
    spec = render_judgment_spec()
    for name, desc in INTENTS.items():
        assert f"  {name}: {desc}" in spec
    assert "  9: 极度危险，先别回，想清楚再说" in spec
    assert "  0: 完全没风险，怎么回都行" in spec
    assert INTENT_QUESTION in spec and RISK_QUESTION in spec and RANK_QUESTION in spec
    assert "0..9 的整数" in spec
    print("questions ok")
