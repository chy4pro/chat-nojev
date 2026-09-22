package com.jev.probe.jev

import org.json.JSONArray
import org.json.JSONObject

/**
 * Fixed Jev question set, ported verbatim from tools/jev/questions.py (the
 * wording that passed calibration). Instructions/criteria in English; chat text
 * stays Chinese.
 *
 * Upstream posted this object to the judgment model as the `questions` field of
 * a decisions request. There is no judgment model here: [renderJudgmentSpec]
 * lays the same text into the prompt of the one call that also writes the
 * candidate replies, and the model answers in the same three answer shapes.
 * The wording — the option keys, their descriptions, the 0-9 tension criteria
 * and the ranking instructions — is unchanged, and this file is still its only
 * home. What changed is how the three choice questions are *answered*: see
 * [CHOICE_ANSWER].
 */
object JevQuestions {

    /**
     * Appended to every question so the D-stage `background` field (relationship,
     * contact notes, knowledge-base hits) reads as given context rather than as
     * an off-topic digression that should be penalized.
     */
    const val BACKGROUND_NOTE =
        " Facts given in background are provided context, not off-topic."

    /**
     * `criteria_order` is not part of the question: it is the order the options
     * were written in, kept beside them so [renderJudgmentSpec] lays them into
     * the prompt in that order. A JSONObject's own key order is up to its
     * implementation, which mattered to nobody while these went to an API and
     * matters now that they are read by a model.
     */
    private const val ORDER = "criteria_order"

    private fun noul(instructions: String, t: String, f: String) = JSONObject().apply {
        put("type", "noul")
        put("instructions", instructions + BACKGROUND_NOTE)
        put("criteria", JSONObject().put("true", t).put("false", f))
        put(ORDER, JSONArray().put("true").put("false"))
    }

    private fun choice(instructions: String, criteria: Map<String, String>) = JSONObject().apply {
        put("type", "choice")
        put("instructions", instructions + BACKGROUND_NOTE)
        put("criteria", JSONObject().also { c -> criteria.forEach { (k, v) -> c.put(k, v) } })
        put(ORDER, JSONArray().also { a -> criteria.keys.forEach { a.put(it) } })
    }

    private fun score(instructions: String, levels: List<String>) = JSONObject().apply {
        put("type", "score")
        put("instructions", instructions + BACKGROUND_NOTE)
        put("criteria", JSONArray().also { a -> levels.forEach { a.put(it) } })
    }

    /** The 7 judgment questions. Returns a fresh JSONObject each call. */
    fun judge(): JSONObject = JSONObject().apply {
        put("literal_question", noul(
            "Is the other person's latest message meant purely literally, with no subtext? " +
                "Judge from the whole thread, not one sentence in isolation.",
            "The latest message is a straightforward statement, question, or plan " +
                "with no implied accusation, test, sarcasm, hint, or unsaid request.",
            "There is subtext: a test of whether you remember or care, sarcasm, " +
                "an implied complaint, a hint they will not say outright, a trap question, " +
                "an accusation dressed as a question, or a cold/short line that really means blame."
        ))
        put("true_intent", choice(
            "What is the other person's true intent in the latest message, given the full conversation? " +
                "Prefer tone and context over surface wording. " +
                "If they are checking whether you remember something or still care, choose confirm_you_care " +
                "even if the words look like a request to 'say it' or to do something. " +
                "If they already accepted and closed the matter peacefully, choose close_topic. " +
                "Ending the relationship, deleting you, or 'don't talk to me' is vent_anger, never close_topic.",
            linkedMapOf(
                "confirm_you_care" to ("They are testing whether you remember, pay attention, or still care. " +
                    "Signals: 'did you forget again', 'then say it', 'you better', sarcastic 'busy person', " +
                    "asking you to prove you know a past conversation. " +
                    "If they mainly want a new deliverable or a yes on a time, do not use this."),
                "vent_anger" to ("They are angry or hurt and mainly want the feeling acknowledged. " +
                    "They are blaming or raising the temperature; a specific plan is not the main point yet."),
                "request_action" to ("They want a concrete action, time, deliverable, or commitment from you now, " +
                    "and this is a real ask, not a loyalty test."),
                "seek_explanation" to ("They want a factual explanation of why something happened. " +
                    "They asked why or what is going on, not mainly for an apology or a new plan."),
                "casual_chat" to ("Light talk, banter, sharing, teasing with a laugh, or friendly logistics " +
                    "with no emotional test and no conflict. A friend suggesting a meal time can be this " +
                    "if the thread is warm."),
                "close_topic" to ("Peaceful wrap-up only: they accepted an apology, confirmed a happy plan, said thanks, " +
                    "or clearly signaled they need nothing more. " +
                    "Not a breakup, not 'don't contact me', not sarcastic 'I'm used to it'.")
            )
        ))
        put("danger_level", score(
            "How close is this conversation to a fight or to hurting the relationship? " +
                "Match the current scene. " +
                "If they genuinely accepted an apology or confirmed a happy plan, score the cooled-down present, " +
                "not an earlier complaint. " +
                "If an ultimatum (break up, report to the boss, stop covering for you) is still in force " +
                "and has not been withdrawn, stay in that high bin even if the latest line names a specific task.",
            listOf(
                "Light chat or joking; no complaint, no test, no deadline.",
                "Mild tease or a small reminder that is easy to laugh off; a clumsy reply would only feel slightly awkward.",
                "A mild complaint or 'please remember next time' said without heat; they still send warm or practical follow-ups.",
                "Noticeable unhappiness; they mention being forgotten, ignored, or kept waiting, but still give you a chance to make it right.",
                "Sarcasm, cold short replies, or 'you better'; they are testing you, and a sloppy or fake-confident reply will escalate.",
                "Openly upset; they accuse you of not listening or not caring; they expect a real response, not a joke.",
                "Clearly angry and blaming you; a wrong reply will turn this into a fight.",
                "Last-chance warning. They will not cover for you, do not want to keep talking unless this changes, " +
                    "or tell you to finish a named checklist yourself because trust is almost gone.",
                "An ultimatum is already on the table even if they also give a practical next step: " +
                    "break up if you forget again, report you tonight, or stop working together if you miss this.",
                "Active rupture: they said it is over, told you not to reply, deleted you, or are exploding."
            )
        ))
        put("should_reply_now", noul(
            "Should your next message contain substantive content? " +
                "Substantive means: admitting a specific known fault, giving a concrete time/plan/deliverable, " +
                "explaining facts you actually know, or reciting the recalled content they asked you to say. " +
                "This is NOT 'should you send any message'. Timing is irrelevant. " +
                "Answer FALSE if the thing they want you to recite or prove is not present in this snippet " +
                "(you would be guessing). 'Then say it' / 'you better' while you are stalling is FALSE. " +
                "Answer FALSE if they already accepted and closed the topic. " +
                "Answer true only if the needed fact, plan, or named fault is already in this snippet.",
            "The needed fact, named fault, or named time/place is already in this snippet, " +
                "and they are waiting for that substance now.",
            "Do not put substance in the next message: the recalled content is not in this snippet, " +
                "they are testing whether you remember, a holding line is enough, " +
                "saying less is safer, or they already closed the topic."
        ))
        put("best_action", choice(
            "What type of next action is best? Do not decide whether to send a message immediately. " +
                "Ignore timing. Choose only the action type. " +
                "If they asked you to recall a specific past message or event and you have not shown that you actually remember it, " +
                "choose check_history - do not apologize or invent a plan instead.",
            linkedMapOf(
                "check_history" to ("Look up prior chat or facts before taking a position. " +
                    "Use when they ask you to repeat, recall, or prove you remember something specific."),
                "apologize" to ("Lead with a sincere apology for a real mistake or hurt already identified. " +
                    "Not for an unnamed forgotten thing when you should first find out what it was."),
                "give_commitment" to ("Give a concrete promise, deadline, or arrangement they asked for " +
                    "in a conflict or work-pressure setting."),
                "explain" to ("Explain what happened or why, without leading with apology or a new plan."),
                "acknowledge" to ("Show you heard them and care, without new facts, an apology, or a plan. " +
                    "Use for light chat or when they mainly need to feel seen."),
                "say_less" to ("Keep it short or add nothing. Extra words would over-explain, reopen a closed topic, " +
                    "or pour fuel on an ultimatum that told you not to talk."),
                "make_plan" to ("Propose or confirm logistics (time, place, task) for a non-conflict request " +
                    "such as a meal or a meeting.")
            )
        ))
        put("she_needs", choice(
            "What does the other person need from you right now? Judge the LATEST message first. " +
                "If they genuinely accepted (thanks / got it / 没事了 / 那就这样 / 收到了 / 过去了), " +
                "you MUST choose nothing, even if earlier they wanted action or an apology. " +
                "Sarcastic 'I'm used to it', 'whatever', 'I don't want to hear it', 'don't bother coming' " +
                "is NOT genuine satisfaction - do not choose nothing. " +
                "If they asked you to recap a named time/place/date, choose action. " +
                "If they are testing whether you remember or still care, and the content is unnamed, choose care.",
            linkedMapOf(
                "apology" to "They need a sincere apology for hurt or a mistake, and they have not accepted one yet.",
                "action" to ("They need a concrete action, time, commitment, recap of a named fact, or follow-through, " +
                    "and they have not yet accepted one."),
                "explanation" to "They need a clear explanation of what happened or why, and have not received it.",
                "care" to ("They need proof you remember, listen, or care - a loyalty or attention test - " +
                    "not yet a plan or an apology. Sarcastic 'I am used to it' belongs here, not nothing."),
                "nothing" to ("They need nothing further. Genuine acceptance, a peaceful closed topic, " +
                    "warm casual chat with no ask, or a rupture where they told you not to reply. " +
                    "Not sarcasm pretending to be fine.")
            )
        ))
        put("tension_resolved", noul(
            "Has interpersonal tension already been resolved? " +
                "Answer true only if there was never tension, or the other person has clearly accepted, " +
                "cooled down, joked again, or said it is fine. " +
                "A sarcastic 'you better', an unanswered test, leftover blame, or an open ultimatum means false.",
            "No remaining tension: they accepted, joked again, said it's fine, " +
                "confirmed a happy plan, or the chat was never tense.",
            "Tension is still present: they are waiting, testing, angry, sarcastic, " +
                "issuing an ultimatum, or the issue is open."
        ))
    }

    /** The 7 question names, in the order they are asked. [judge] is keyed by
     *  these, and the prompt is rendered in this order. */
    val QUESTION_ORDER = listOf(
        "literal_question", "true_intent", "danger_level",
        "should_reply_now", "best_action", "she_needs", "tension_resolved")

    /** The ranking keys, in the order they map onto `replies`. */
    val REPLY_KEYS = listOf("reply_a", "reply_b", "reply_c")

    /**
     * The ranking question, word for word the one the judgment model was asked
     * over the already-drafted candidates. The same call writes the candidates
     * now, so they cannot be pasted into `criteria` in advance; the
     * instructions are unchanged and the options are the three reply keys.
     */
    const val RANK_INSTRUCTIONS =
        "Which candidate reply is the most appropriate next message, " +
            "given the conversation and the other person's true need? " +
            "Prefer a reply that matches the best action type. " +
            "Penalize dismissive, over-promising, or off-topic replies. " +
            "If the facts are not yet confirmed, prefer the candidate that looks them up " +
            "instead of faking memory or a vague apology." + BACKGROUND_NOTE

    /**
     * How the three choice questions are to be answered. Upstream's judgment
     * model was a classifier: it could only return one of the fixed English
     * keys, and the overlay carried a table translating each key into Chinese
     * for display. A general model does not need a closed set, so it names the
     * option it picked in the language of the conversation and the panel shows
     * that phrase as written. The keys and their descriptions above are
     * unchanged — they still frame the question.
     */
    private const val CHOICE_ANSWER =
        "The option keys above are English so the descriptions can be precise; they are not what " +
            "you answer with. Answer with a short phrase naming the option you picked, written in " +
            "the language of the conversation. If none of the options fits, write your own short " +
            "phrase in that same language. Never answer with the English key."

    /**
     * The answer shape asked of the model, one per question type. These are the
     * three shapes the judgment API itself answered in, so what comes back can
     * be moved into [com.jev.probe.core.Analysis] by translation alone.
     */
    private fun shapeOf(kind: String, maxLevel: Int): String = when (kind) {
        "noul" -> """{"noul": <number 0..1 = probability that the "true" description below fits>}"""
        "choice" -> """{"choice": "<the option you pick, as a short phrase in the language of the """ +
            """conversation>", "confidence": <number 0..1>, """ +
            """"probabilities": {"<option, phrased the same way>": <number 0..1>, ...}}  """ +
            "(one entry per option you weighed, values summing to 1)"
        else -> """{"score": <integer 0..$maxLevel>, "confidence": <number 0..1>, """ +
            """"probabilities": {"<level 0..$maxLevel as a string>": <number 0..1>, ...}}"""
    }

    /** Highest level of a score question (`criteria` is one description per level). */
    fun maxLevel(question: JSONObject): Int = (question.optJSONArray("criteria")?.length() ?: 10) - 1

    /**
     * The question set flattened into prompt text: one block per question, with
     * the instructions and every criterion copied out verbatim. This is the only
     * consumer of [judge] now — the wording still lives there and nowhere else.
     *
     * Criterion order for choice and noul questions is the order [judge] put
     * them in (Android's org.json keeps insertion order); score levels are a
     * JSONArray and are always in order, the index being the score itself.
     */
    fun renderJudgmentSpec(questions: JSONObject = judge()): String {
        val blocks = ArrayList<String>()
        for (name in QUESTION_ORDER) {
            val q = questions.optJSONObject(name) ?: continue
            val kind = q.optString("type")
            blocks.add("### " + name + "\nAnswer shape: " + shapeOf(kind, maxLevel(q)) + "\n" +
                q.optString("instructions") + "\n" + criteriaLines(q) +
                if (kind == "choice") "\n" + CHOICE_ANSWER else "")
        }
        blocks.add("""### best_reply""" + "\n" + """Answer shape: the top-level "best_reply" and """ +
            """"reply_scores" fields described above, keyed """ + REPLY_KEYS.joinToString(", ") +
            """ in the same order as "replies".""" + "\n" + RANK_INSTRUCTIONS)
        return blocks.joinToString("\n\n")
    }

    /** `criteria` spelled out: label and description per line in the order they
     *  were written, or one line per score level carrying the level number,
     *  which is what its index means. */
    private fun criteriaLines(q: JSONObject): String {
        q.optJSONArray("criteria")?.let { levels ->
            return (0 until levels.length()).joinToString("\n") { "  $it: " + levels.optString(it) }
        }
        val c = q.optJSONObject("criteria") ?: return ""
        val order = q.optJSONArray(ORDER)
        val keys = if (order != null) (0 until order.length()).map { order.optString(it) }
        else c.keys().asSequence().toList()
        return keys.joinToString("\n") { k -> "  " + k + ": " + c.optString(k) }
    }
}
