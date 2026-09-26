package com.jev.probe.jev

import android.util.Log
import com.jev.probe.core.Analysis
import com.jev.probe.core.ChatSnapshot
import com.jev.probe.core.Prefs
import com.jev.probe.core.kb.ChatContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * The one model route: any OpenAI-compatible `/chat/completions` endpoint.
 * Reads replyBaseUrl / replyKey / replyModel from [Prefs].
 *
 * Upstream drafted the candidates here and sent the conversation a second time
 * to a separate judgment model (TypeSafe Jev) for the 7 judgment questions and
 * the ranking. Here the same call does both: the question text is laid into
 * this prompt by [JevQuestions.renderJudgmentSpec], and the model answers it
 * while it writes the candidates. [MergedAnswer] moves what comes back into the
 * shapes the judgment API answered in; nothing downstream changed.
 */
class ReplyClient(private val prefs: Prefs) {

    /**
     * The 7 judgment questions, 3 candidate replies and their ranking, in one
     * call. Errors are returned inside [Analysis.error], not thrown — upstream's
     * judgment call had the same contract, and it is now the only call there is.
     *
     * @param ctx D-stage knowledge context. When present its background and
     *        history are prepended to the prompt with an instruction to stay
     *        consistent with them and invent nothing beyond them.
     */
    fun draftAndJudge(snapshot: ChatSnapshot, relationship: String, ctx: ChatContext? = null): Analysis {
        val start = System.currentTimeMillis()
        return try {
            val convo = snapshot.messages.takeLast(10).joinToString("\n") {
                (if (it.side == "me") "我" else "对方") + "：" + it.text
            }
            val user = knowledgeBlock(relationship, ctx) +
                "关系：$relationship\n\n最近对话：\n$convo\n\n" +
                "请给出那一个 JSON 对象，字段按 judgment → replies → best_reply → reply_scores 的顺序写：" +
                "judgment 给 7 个字段，replies 恰好 3 条，best_reply 点名其中一条，" +
                "reply_scores 给每条一个 0~1 的分。"
            MergedAnswer.parse(chat(system(), user, temperature = 0.8),
                System.currentTimeMillis() - start)
        } catch (e: Exception) {
            Log.w(TAG, "draftAndJudge failed: ${e.message}")
            Analysis(null, null, null, null, null, null, null, emptyList(),
                System.currentTimeMillis() - start, error = e.message ?: "回复接口请求失败")
        }
    }

    /**
     * The system prompt: upstream's drafting rules, then what the one object
     * must contain and in which order, then the judgment questions laid out
     * verbatim by [JevQuestions.renderJudgmentSpec] (their wording lives only
     * there).
     *
     * Field order is part of the contract, and `judgment` comes first on
     * purpose: the model writes the object left to right, so the three
     * candidates are produced after — and conditioned on — the judgment it has
     * just written. That is what upstream gets by feeding its judgment call's
     * result into the drafting call. [MergedAnswer] reads by key, so nothing
     * downstream depends on the order.
     */
    private fun system(): String =
        "你是中文即时通讯回复助手。先读完整段对话把 judgment 那 7 个字段答完，再据此写 3 条候选回复，" +
            "三条策略要有区别（例如：一条稳妥承接、一条给具体行动或承诺、一条简短低姿态）。" +
            "每条不超过 40 字，口语、自然、像真人在聊天软件里发消息。\n" +
            "输出：只输出一个 JSON 对象，别的什么都别写——不要 Markdown 围栏，不要解释，不要前言后语。\n" +
            "字段必须按下面的先后顺序写出来，不要调换：\n" +
            "- \"judgment\"：对象，下面 7 个字段各给一条。字段名和分数档位必须一字不差照抄下面的说明；" +
            "choice 那几道题答的是一句短语（按下面的说明，用对话那门语言写），不是英文 key；" +
            "哪一条你判断不出来就把那一条整个省掉，不要填 null、不要瞎猜。\n" +
            "- \"replies\"：恰好 3 个字符串的数组，就是上面那 3 条消息本身，不要编号、不要前缀。" +
            "这 3 条必须顺着你在 judgment 里刚写下的 best_action（建议动作）、true_intent（对方真实意图）" +
            "和 she_needs（对方要什么）来写，不要和它们打架；\n" +
            "- \"best_reply\"：\"reply_a\" / \"reply_b\" / \"reply_c\" 之一，" +
            "它们依次对应 replies 里的第 1、2、3 条；\n" +
            "- \"reply_scores\"：对象，键就是上面那三个，值是 0 到 1 之间的小数，三个加起来等于 1。\n" +
            "所有字符串用双引号，不要有尾逗号，不要写注释。\n\n" +
            "判断说明（judgment 那 7 个字段照这里答；说明是英文的，聊天内容仍然是中文）：\n\n" +
            JevQuestions.renderJudgmentSpec()

    /** The background + history preamble; empty string when there is no context. */
    private fun knowledgeBlock(relationship: String, ctx: ChatContext?): String {
        ctx ?: return ""
        val background = ctx.background(relationship)
        val history = ctx.history
        if (background.isBlank() && history.isEmpty()) return ""
        val sb = StringBuilder()
        sb.append("以下是关于我和对方的背景与知识库，回复必须与之一致，")
            .append("可以直接引用其中事实，不要编造知识库里没有的事实。\n")
        if (background.isNotBlank()) sb.append(background).append('\n')
        if (history.isNotEmpty()) {
            sb.append("\n更早的聊天记录（越靠下越新）：\n")
            history.takeLast(prefs.contextHistoryCount.coerceIn(0, 100)).forEach {
                sb.append(if (it.side == "me") "我：" else "对方：").append(it.text).append('\n')
            }
        }
        sb.append('\n')
        return sb.toString()
    }

    /**
     * One plain chat round trip for the settings connectivity test. Deliberately
     * NOT [summarize]: the test should exercise the ordinary path, not whatever
     * the summary prompt happens to be.
     */
    fun ping(): String =
        chat("你是连通性测试助手，只按要求回答，不要解释。", "请只回复两个字：收到", temperature = 0.0).trim()

    /** Condense a block of text (used by the D-stage contact auto-summary). */
    fun summarize(text: String): String {
        if (text.isBlank()) return ""
        val sys = "你是中文摘要助手。把给到的聊天记录压缩成不超过 120 字的第三人称要点摘要，" +
            "只保留事实、偏好、承诺和待办，不要评论，不要编造。直接输出摘要正文。"
        return chat(sys, text, temperature = 0.2).trim()
    }

    /** One chat-completions round trip; returns the assistant message content. */
    private fun chat(system: String, user: String, temperature: Double): String {
        val url = prefs.replyEndpoint()
        val messages = JSONArray()
            .put(JSONObject().put("role", "system").put("content", system))
            .put(JSONObject().put("role", "user").put("content", user))
        val body = JSONObject()
            .put("model", prefs.replyModel)
            .put("messages", messages)
            .put("temperature", temperature)
        val resp = HttpJson.post(url, prefs.effectiveReplyKey(), body, Route.REPLY, HttpJson.headersFor(url))
        return resp.optJSONArray("choices")?.optJSONObject(0)
            ?.optJSONObject("message")?.optString("content") ?: ""
    }

    companion object { private const val TAG = "JEVASSIST" }
}
