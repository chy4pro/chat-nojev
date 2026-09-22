package com.jev.probe.jev

import com.jev.probe.core.Analysis
import com.jev.probe.core.Choice
import com.jev.probe.core.RankedReply
import com.jev.probe.core.Score
import org.json.JSONArray
import org.json.JSONObject

/**
 * One model answer -> [Analysis]: the candidate replies, the ranking and the 7
 * judgment fields that upstream got from a separate judgment model.
 *
 * This layer does two things and stops there.
 *
 * 1. **Parsing.** Digging the JSON object out of whatever the model wrote —
 *    a code fence, a preamble, a truncated object, or no JSON at all (then the
 *    line-by-line fallback upstream's ReplyClient used, padding included).
 * 2. **Translation.** Moving the values into the three answer shapes the
 *    judgment API answered in (`noul` / `choice` / `score`, plus the
 *    `best_reply` ranking block), and then running the *same* parse functions
 *    upstream's JudgeClient ran on them — [parseChoice], [parseScore] and
 *    [parseRanked] below are its code, unchanged.
 *
 * It deliberately does **not** validate. It does not check a phrase against the
 * option keys, does not clamp a score into 0..9, does not normalize the reply
 * scores, and does not invent a value the model left out. Upstream had to
 * survive its own judgment model returning junk, so the consumers already cope:
 * [com.jev.probe.overlay.OverlayController] prints whatever phrase it is given
 * and shows "危险 11/9" for a score of 11, and [parseRanked] turns a score it
 * cannot read into 0.0. Re-validating here would not protect anything; it would
 * make the same bad value behave differently than it does upstream.
 *
 * A value the model did not give is left out, the way upstream's answers came
 * back without a field its model did not answer — the corresponding
 * [Analysis] field then stays null. The one place a number appears that the
 * model did not write is [Choice.confidence] / [Score.confidence], which are
 * not nullable: `optDouble(..., 0.0)` below is upstream's own parse, kept so a
 * missing confidence renders as "把握 0%" exactly as it does upstream.
 *
 * `confidence` and `probabilities` are now the model's **self-report**: the
 * model that wrote the candidates also rated them and its own certainty. They
 * are not the calibrated outputs of a classifier, and nothing downstream should
 * be read as if they were.
 */
object MergedAnswer {

    /** Question type per name, derived from the question set — the taxonomy has
     *  exactly one home and it is [JevQuestions]. */
    private val KIND: Map<String, String> = JevQuestions.judge().let { qs ->
        JevQuestions.QUESTION_ORDER.mapNotNull { name ->
            qs.optJSONObject(name)?.let { name to it.optString("type") }
        }.toMap()
    }

    /** Highest level of each score question, from the same place. */
    private val MAX_LEVEL: Map<String, Int> = JevQuestions.judge().let { qs ->
        KIND.filterValues { it == "score" }.keys.mapNotNull { name ->
            qs.optJSONObject(name)?.let { name to JevQuestions.maxLevel(it) }
        }.toMap()
    }

    /** What upstream's ReplyClient padded a short candidate list with. */
    private const val PAD = "（稍等，我看下）"

    /**
     * @param content the raw assistant message.
     * @param latencyMs how long the one call took, for [Analysis.latencyMs].
     */
    fun parse(content: String, latencyMs: Long): Analysis {
        val obj = jsonObject(content)
        val candidates = replies(obj, content)
        val answers = judgment(obj)
        return Analysis(
            trueIntent = parseChoice(answers.optJSONObject("true_intent")),
            dangerLevel = parseScore(answers.optJSONObject("danger_level"),
                MAX_LEVEL["danger_level"] ?: 9),
            sheNeeds = parseChoice(answers.optJSONObject("she_needs")),
            shouldReplyNow = answers.optJSONObject("should_reply_now")?.optDouble("noul"),
            bestAction = parseChoice(answers.optJSONObject("best_action")),
            tensionResolved = answers.optJSONObject("tension_resolved")?.optDouble("noul"),
            literalQuestion = answers.optJSONObject("literal_question")?.optDouble("noul"),
            rankedReplies = parseRanked(ranking(obj), candidates),
            latencyMs = latencyMs
        )
    }

    // ------------------------------------------------------------- parsing

    /** The JSON object in the model's answer. Code fences and any prose around
     *  it are tolerated; a truncated object yields an empty one and the callers
     *  fall back to reading what text is there. */
    fun jsonObject(content: String): JSONObject {
        val stripped = content.trim()
            .removePrefix("```json").removePrefix("```").removeSuffix("```").trim()
        val first = stripped.indexOf('{')
        val last = stripped.lastIndexOf('}')
        for (text in listOf(stripped, if (first in 0 until last) stripped.substring(first, last + 1) else "")) {
            if (text.isEmpty()) continue
            try { return JSONObject(text) } catch (_: Exception) { }
        }
        return JSONObject()
    }

    /**
     * The candidate replies: the object's `replies` array when it parsed, else
     * upstream's own text fallback over the whole answer.
     *
     * The list is padded to 3 with [PAD] exactly as upstream's
     * `ReplyClient.parseThree` padded it, so a model that writes fewer replies
     * than it was asked for produces the same three cards it does upstream.
     */
    fun replies(obj: JSONObject, content: String): List<String> {
        val raw = obj.optJSONArray("replies")
        if (raw != null && raw.length() > 0) {
            val out = ArrayList<String>()
            // Trimmed and kept as they come, blanks included: upstream's reader
            // of the bare array did the same, and dropping one here would be
            // this layer deciding the model wrote one reply fewer than it did.
            for (i in 0 until raw.length()) out.add(raw.optString(i).trim())
            return pad(out.take(3))
        }
        return parseThree(content)
    }

    /** Upstream `ReplyClient.parseThree`, unchanged: the bare-array answer, then
     *  the line-by-line fallback, then the padding. */
    private fun parseThree(content: String): List<String> {
        val start = content.indexOf('[')
        val end = content.lastIndexOf(']')
        if (start >= 0 && end > start) {
            try {
                val arr = JSONArray(content.substring(start, end + 1))
                val out = ArrayList<String>()
                for (i in 0 until arr.length()) out.add(arr.getString(i).trim())
                if (out.size >= 3) return out.take(3)
                return pad(out)
            } catch (_: Exception) { }
        }
        val lines = content.split("\n").map { it.trim().trimStart('-', '*', '1', '2', '3', '.', ' ', '"') }
            .filter { it.isNotBlank() }
        return pad(lines.take(3))
    }

    private fun pad(items: List<String>): List<String> {
        val out = items.toMutableList()
        while (out.size < 3) out.add(PAD)
        return out
    }

    // --------------------------------------------------------- translation

    /**
     * The 7 judgment fields in the shapes the judgment API answered in. The
     * model is asked to nest them under `judgment`; some put them at the top
     * level instead, and both are read. A field the model did not answer is
     * simply absent, and so is a field whose value it left out.
     */
    fun judgment(obj: JSONObject): JSONObject {
        val raw = obj.optJSONObject("judgment") ?: obj
        val answers = JSONObject()
        for ((name, kind) in KIND) {
            if (!raw.has(name)) continue
            answerFor(kind, raw.opt(name))?.let { answers.put(name, it) }
        }
        return answers
    }

    /** One judgment value -> one judgment-API answer object, or null when the
     *  model gave no value. Keys are moved; values are passed through as they
     *  are — no taxonomy check, no range clamp, no normalizing. */
    private fun answerFor(kind: String, raw: Any?): JSONObject? {
        if (raw == null || raw == JSONObject.NULL) return null
        if (kind == "noul") {
            // A bare number and {"noul": x} are both common; true/false is the
            // model saying 1 / 0, which is what a noul probability of 1 or 0 is.
            var value: Any? = if (raw is JSONObject) raw.opt("noul") else raw
            if (value is Boolean) value = if (value) 1.0 else 0.0
            if (value == null || value == JSONObject.NULL) return null
            return JSONObject().put("type", "noul").put("noul", value)
        }
        val field = if (kind == "choice") "choice" else "score"
        val o = if (raw is JSONObject) raw else JSONObject().put(field, raw)  // bare value too
        val value = o.opt(field)
        if (value == null || value == JSONObject.NULL) return null
        val out = JSONObject().put("type", kind).put(field, value)
        o.opt("confidence")?.takeIf { it != JSONObject.NULL }?.let { out.put("confidence", it) }
        o.opt("probabilities")?.takeIf { it != JSONObject.NULL }?.let { out.put("probabilities", it) }
        return out
    }

    /**
     * The ranking, in the shape the judgment API's `best_reply` answer had:
     * the named winner, the score it gave that one, and the per-candidate
     * scores under the reply keys. Null when the model produced neither, which
     * is what upstream saw when its answer carried no `best_reply` — every
     * candidate then scores 0.0 and the order is left alone.
     *
     * The scores are keyed by position, `reply_a/b/c` against `replies[0..2]`,
     * because that is what the model was told they mean. They are passed
     * through unchanged: not rescaled, not normalized, not range-checked.
     *
     * Note that this app never consulted the named winner: [parseRanked] sorts
     * by score alone (upstream did the same, over the same field). The name is
     * carried anyway rather than dropped, because dropping it would be this
     * layer deciding what downstream is allowed to see.
     */
    fun ranking(obj: JSONObject): JSONObject? {
        val choice = obj.opt("best_reply")?.takeIf { it != JSONObject.NULL }
        val scores = obj.optJSONObject("reply_scores")
        if (choice == null && scores == null) return null
        val out = JSONObject().put("type", "choice")
        if (choice != null) out.put("choice", choice)
        val probabilities = JSONObject()
        if (scores != null) {
            for (key in JevQuestions.REPLY_KEYS) {
                scores.opt(key)?.takeIf { it != JSONObject.NULL }?.let { probabilities.put(key, it) }
            }
        }
        // Upstream's choice answers carried the probability of the option they
        // picked as `confidence`; that is the score the model gave the reply it
        // named, not a new number. Absent when it scored no such reply.
        (choice as? String)?.let { name ->
            probabilities.opt(name)?.let { out.put("confidence", it) }
        }
        return out.put("probabilities", probabilities)
    }

    // ------------------------------------ upstream JudgeClient's parsers, as-is

    private fun parseChoice(o: JSONObject?): Choice? {
        o ?: return null
        val probs = HashMap<String, Double>()
        o.optJSONObject("probabilities")?.let { p ->
            p.keys().forEach { k -> probs[k] = p.optDouble(k) }
        }
        return Choice(o.optString("choice"), o.optDouble("confidence", 0.0), probs)
    }

    /**
     * Upstream read the level count off the `legend` the judgment API sent with
     * a score answer, falling back to 9. There is no legend to send here, so the
     * fallback is the level count of the question we actually asked — the same 9,
     * and it follows the question set if that ever changes.
     */
    private fun parseScore(o: JSONObject?, maxLevel: Int): Score? {
        o ?: return null
        val legend = o.optJSONObject("legend")
        val max = legend?.keys()?.asSequence()?.mapNotNull { it.toIntOrNull() }?.maxOrNull() ?: maxLevel
        return Score(o.optDouble("score", 0.0), o.optDouble("confidence", 0.0), max)
    }

    private fun parseRanked(o: JSONObject?, candidates: List<String>): List<RankedReply> {
        val keys = JevQuestions.REPLY_KEYS
        val probs = o?.optJSONObject("probabilities")
        val list = candidates.mapIndexed { i, text ->
            RankedReply(text, probs?.optDouble(keys.getOrElse(i) { "" }, 0.0) ?: 0.0)
        }
        return list.sortedByDescending { it.prob }
    }
}
