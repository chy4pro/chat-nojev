/*
 * Differential test for the Android app: prove that the stretch after the API
 * behaves identically here and upstream.
 *
 * The same judgment values and the same candidate replies go in; upstream's
 * `JevClient.analyze()` and ours must come back with the same [Analysis] — the
 * same typed Choice / Score objects, the same nulls, the same ranked replies in
 * the same order. Upstream's values arrive over three calls (judge, draft,
 * rank); ours over one merged call. The producer changed; nothing downstream
 * was allowed to.
 *
 * The standard is "same values in, same object out": the adapter moves keys and
 * does not validate, so bad values — a phrase outside the taxonomy, a score of
 * 11 — must come through identically too, because the consumers were already
 * hardened against a judgment model producing them.
 *
 * Each scenario is written once as neutral data (candidates, who won, the
 * per-candidate scores, the 7 judgment values) and rendered into the two wire
 * formats by [renderUpstream] and [renderOurs].
 *
 * Where it is stubbed (neither side touches the network): only
 * `jev/HttpJson.post`, replaced by tools/equivalence_stub/HttpJson.kt. Both
 * sides therefore run their real prompt, request body, parser and translation.
 * Ours is handed the raw model text and runs MergedAnswer on it, not a
 * pre-parsed structure.
 *
 * Both trees declare the same packages, so each side is compiled into its own
 * output directory and run as its own JVM (see equivalence_check_android.sh):
 *
 *     java ... Equivalence_check_androidKt --side upstream --out up.json
 *     java ... Equivalence_check_androidKt --side ours     --out ours.json
 *     java ... Equivalence_check_androidKt --compare up.json ours.json
 *
 * Results go to a file, not stdout: android.util.Log's stand-in prints there.
 */

import com.jev.probe.core.Analysis
import com.jev.probe.core.ChatSnapshot
import com.jev.probe.core.Msg
import com.jev.probe.core.Prefs
import com.jev.probe.jev.HttpJson
import com.jev.probe.jev.JevClient
import com.jev.probe.jev.Route
import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import kotlin.system.exitProcess

// ---------------------------------------------------------------------------
// Neutral data. The question types are spelled out here so the scenarios depend
// on neither tree; both sides check them against the real question set.
// ---------------------------------------------------------------------------

val FIELD_KIND = linkedMapOf(
    "literal_question" to "noul",
    "true_intent" to "choice",
    "danger_level" to "score",
    "should_reply_now" to "noul",
    "best_action" to "choice",
    "she_needs" to "choice",
    "tension_resolved" to "noul"
)

val REPLY_KEYS = listOf("reply_a", "reply_b", "reply_c")

/** A full set of judgment values; scenarios override or drop fields. */
val FULL_JUDGMENT: Map<String, Map<String, Any>> = linkedMapOf(
    "literal_question" to mapOf("noul" to 0.85),
    "true_intent" to mapOf("choice" to "vent_anger", "confidence" to 0.62,
        "probabilities" to mapOf("vent_anger" to 0.62, "seek_explanation" to 0.23,
            "casual_chat" to 0.15)),
    "danger_level" to mapOf("score" to 4, "confidence" to 0.55,
        "probabilities" to mapOf("3" to 0.2, "4" to 0.55, "5" to 0.25)),
    "should_reply_now" to mapOf("noul" to 0.7),
    "best_action" to mapOf("choice" to "acknowledge", "confidence" to 0.5,
        "probabilities" to mapOf("acknowledge" to 0.5, "apologize" to 0.3, "explain" to 0.2)),
    "she_needs" to mapOf("choice" to "care", "confidence" to 0.44,
        "probabilities" to mapOf("care" to 0.44, "apology" to 0.31, "nothing" to 0.25)),
    "tension_resolved" to mapOf("noul" to 0.12)
)

val MESSAGES = listOf(
    Msg("other", "你昨天说的那个事到底怎么样了"),
    Msg("me", "还在弄"),
    Msg("other", "你是不是忘了")
)

/** [FULL_JUDGMENT] with fields replaced, or dropped when the value is null. */
fun judgment(vararg overrides: Pair<String, Map<String, Any>?>): Map<String, Map<String, Any>> {
    val out = LinkedHashMap(FULL_JUDGMENT)
    for ((name, value) in overrides) if (value == null) out.remove(name) else out[name] = value
    return out
}

class Scn(
    val name: String,
    val note: String,
    val candidates: List<String>,
    /** reply_a/b/c, one that points at no candidate, or null for "no winner named". */
    val winner: String?,
    /** per-candidate scores, or null for "no scores at all". */
    val probs: Map<String, Any>?,
    val judgmentValues: Map<String, Map<String, Any>> = FULL_JUDGMENT,
    /** Upstream's score answers carried a `legend`; one scenario sends one. */
    val legend: Boolean = false,
    /** Our side spells the nouls as bare numbers / booleans, as models do. */
    val nounBare: Boolean = false,
    /** Known, explained differences, as key prefixes. */
    val expectDiff: List<String> = emptyList()
)

val C3 = listOf("还在跟进 今晚给你信", "没忘 就是卡在对面那边", "我这就去催")
val C2 = listOf("没忘 今晚给你信", "我这就去催")

val SCENARIOS = listOf(
    Scn("ordinary_three_candidates", "普通情况：三条候选，赢家清楚", C3, "reply_b",
        mapOf("reply_a" to 0.25, "reply_b" to 0.55, "reply_c" to 0.20)),
    Scn("winner_not_top_scorer", "点名的不是分最高的那条：两边都只按分排序", C3, "reply_c",
        mapOf("reply_a" to 0.55, "reply_b" to 0.30, "reply_c" to 0.15)),
    Scn("winner_is_reply_a", "赢家是第一条：验键到下标的映射", C3, "reply_a",
        mapOf("reply_a" to 0.40, "reply_b" to 0.35, "reply_c" to 0.25)),
    Scn("winner_is_reply_c", "赢家是第三条：验键到下标的映射", C3, "reply_c",
        mapOf("reply_a" to 0.20, "reply_b" to 0.30, "reply_c" to 0.50)),
    Scn("two_candidates_only", "模型只给两条：两边都补足三条（占位文案相同）", C2, "reply_b",
        mapOf("reply_a" to 0.35, "reply_b" to 0.65)),
    Scn("boundary_values_low", "边界值：danger_level 0，noul 全 0.0", C3, "reply_a",
        mapOf("reply_a" to 1.0, "reply_b" to 0.0, "reply_c" to 0.0),
        judgment(
            "literal_question" to mapOf("noul" to 0.0),
            "should_reply_now" to mapOf("noul" to 0.0),
            "tension_resolved" to mapOf("noul" to 0.0),
            "danger_level" to mapOf("score" to 0, "confidence" to 0.9,
                "probabilities" to mapOf("0" to 0.9, "1" to 0.1)))),
    Scn("boundary_values_high", "边界值：danger_level 9，noul 全 1.0", C3, "reply_c",
        mapOf("reply_a" to 0.0, "reply_b" to 0.0, "reply_c" to 1.0),
        judgment(
            "literal_question" to mapOf("noul" to 1.0),
            "should_reply_now" to mapOf("noul" to 1.0),
            "tension_resolved" to mapOf("noul" to 1.0),
            "danger_level" to mapOf("score" to 9, "confidence" to 1.0,
                "probabilities" to mapOf("8" to 0.0, "9" to 1.0)))),
    Scn("ranking_block_missing", "排序整块都没有：两边都全记 0、顺序不动", C3, null, null),
    Scn("choice_names_missing_candidate", "点名的候选不存在（只有两条却点 reply_c）", C2, "reply_c",
        mapOf("reply_a" to 0.40, "reply_b" to 0.60)),
    Scn("probabilities_not_normalized", "分加起来不是 1：两边都原样递下去", C3, "reply_b",
        mapOf("reply_a" to 2.0, "reply_b" to 5.0, "reply_c" to 3.0)),
    Scn("score_out_of_range", "紧张度 11（题目只有 0..9）：不夹范围，原样递下去", C3, "reply_b",
        mapOf("reply_a" to 0.25, "reply_b" to 0.55, "reply_c" to 0.20),
        judgment("danger_level" to mapOf("score" to 11, "confidence" to 0.3,
            "probabilities" to mapOf("9" to 0.3)))),
    Scn("intent_outside_taxonomy", "题目里没有的说法：不查表，原样递下去", C3, "reply_b",
        mapOf("reply_a" to 0.25, "reply_b" to 0.55, "reply_c" to 0.20),
        judgment("true_intent" to mapOf("choice" to "他想吃饭", "confidence" to 0.4,
            "probabilities" to mapOf("他想吃饭" to 0.4, "vent_anger" to 0.6)))),
    Scn("judgment_field_absent", "两边都缺同一个判断字段（best_action）", C3, "reply_a",
        mapOf("reply_a" to 0.5, "reply_b" to 0.3, "reply_c" to 0.2),
        judgment("best_action" to null)),
    Scn("confidence_omitted", "判断字段不给 confidence（判断模型一定给，通用模型常常漏）", C3, "reply_b",
        mapOf("reply_a" to 0.3, "reply_b" to 0.5, "reply_c" to 0.2),
        judgment(
            "true_intent" to mapOf("choice" to "vent_anger",
                "probabilities" to mapOf("vent_anger" to 0.62, "casual_chat" to 0.38)),
            "danger_level" to mapOf("score" to 4,
                "probabilities" to mapOf("4" to 0.7, "5" to 0.3)))),
    Scn("noul_as_bare_number", "我们这边 noul 写成裸数字 / true-false，必须等价于 {\"noul\": x}", C3, "reply_a",
        mapOf("reply_a" to 0.6, "reply_b" to 0.25, "reply_c" to 0.15),
        judgment(
            "literal_question" to mapOf("noul" to 1.0),
            "should_reply_now" to mapOf("noul" to 0.0),
            "tension_resolved" to mapOf("noul" to 0.3)),
        nounBare = true),
    Scn("score_legend_from_api", "jev-chat-jarvis 那边带 legend（接口就是这么回的），我们没有：档数必须一样", C3, "reply_b",
        mapOf("reply_a" to 0.25, "reply_b" to 0.55, "reply_c" to 0.20),
        legend = true)
)

// ---------------------------------------------------------------------------
// The two wire formats
// ---------------------------------------------------------------------------

fun jsonOf(value: Any?): Any? = when (value) {
    is Map<*, *> -> JSONObject().also { o -> value.forEach { (k, v) -> o.put(k.toString(), jsonOf(v)) } }
    is List<*> -> JSONArray().also { a -> value.forEach { a.put(jsonOf(it)) } }
    else -> value
}

/** Neutral data -> the `answers` object the judgment API returned. */
fun renderUpstreamAnswers(scn: Scn): JSONObject {
    val answers = JSONObject()
    for ((name, spec) in scn.judgmentValues) {
        val kind = FIELD_KIND[name] ?: error("unknown field $name")
        val o = JSONObject().put("type", kind)
        when (kind) {
            "noul" -> o.put("noul", spec["noul"])
            "choice" -> o.put("choice", spec["choice"])
            else -> o.put("score", spec["score"])
        }
        spec["confidence"]?.let { o.put("confidence", it) }
        spec["probabilities"]?.let { o.put("probabilities", jsonOf(it)) }
        if (kind == "score" && scn.legend) {
            // What the API sent alongside a score: the level descriptions, keyed
            // by level. Only its highest key is read (Score.maxLevel).
            val legend = JSONObject()
            for (i in 0..9) legend.put(i.toString(), "level $i")
            o.put("legend", legend)
        }
        answers.put(name, o)
    }
    return answers
}

/** The `answers` object of the separate ranking call. */
fun renderUpstreamRanking(scn: Scn): JSONObject {
    if (scn.winner == null && scn.probs == null) return JSONObject()
    val block = JSONObject().put("type", "choice")
    scn.winner?.let { block.put("choice", it) }
    scn.winner?.let { w -> scn.probs?.get(w)?.let { block.put("confidence", it) } }
    block.put("probabilities", jsonOf(scn.probs ?: emptyMap<String, Any>()))
    return JSONObject().put("best_reply", block)
}

/** Neutral data -> the one object our merged call comes back with, written the
 *  way a model writes it (fenced, indented). */
fun renderOurs(scn: Scn): String {
    val obj = JSONObject()
    obj.put("replies", JSONArray().also { a -> scn.candidates.forEach { a.put(it) } })
    scn.winner?.let { obj.put("best_reply", it) }
    scn.probs?.let { obj.put("reply_scores", jsonOf(it)) }
    val judgmentOut = JSONObject()
    for ((name, spec) in scn.judgmentValues) {
        when (FIELD_KIND[name]) {
            "noul" -> {
                val noul = spec["noul"]
                judgmentOut.put(name, when {
                    !scn.nounBare -> JSONObject().put("noul", noul)
                    noul == 1.0 -> true          // a model often answers the
                    noul == 0.0 -> false         // noul questions true / false
                    else -> noul                 // or as a bare probability
                })
            }
            else -> {
                val o = JSONObject()
                if (FIELD_KIND[name] == "choice") o.put("choice", spec["choice"])
                else o.put("score", spec["score"])
                spec["confidence"]?.let { o.put("confidence", it) }
                spec["probabilities"]?.let { o.put("probabilities", jsonOf(it)) }
                judgmentOut.put(name, o)
            }
        }
    }
    obj.put("judgment", judgmentOut)
    return "```json\n" + obj.toString(2) + "\n```"
}

/** The chat-completions envelope both sides' reply route unwraps. */
fun chatEnvelope(content: String): JSONObject = JSONObject().put("choices",
    JSONArray().put(JSONObject().put("message", JSONObject().put("content", content))))

// ---------------------------------------------------------------------------
// Running one side
// ---------------------------------------------------------------------------

fun prefs(): Prefs {
    val p = Prefs(FakeContext(), "equivalence_check")
    p.replyBaseUrl = "https://example.invalid/v1"
    p.replyKey = "test-key-not-a-real-key"
    p.replyModel = "test-model"
    return p
}

fun runSide(side: String): JSONObject {
    val out = JSONObject()
    for (scn in SCENARIOS) {
        HttpJson.reset()
        if (side == "upstream") {
            // judge -> draft -> rank, in that order, on two routes.
            HttpJson.enqueue(Route.JUDGE, JSONObject().put("answers", renderUpstreamAnswers(scn)))
            HttpJson.enqueue(Route.REPLY, chatEnvelope(
                JSONArray().also { a -> scn.candidates.forEach { a.put(it) } }.toString()))
            HttpJson.enqueue(Route.JUDGE, JSONObject().put("answers", renderUpstreamRanking(scn)))
        } else {
            HttpJson.enqueue(Route.REPLY, chatEnvelope(renderOurs(scn)))
        }
        val snapshot = ChatSnapshot("差分测试", MESSAGES)
        val analysis = JevClient(prefs()).analyze(snapshot, "朋友")
        out.put(scn.name, JSONObject()
            .put("result", flattenAnalysis(analysis))
            .put("calls", JSONArray().also { a -> HttpJson.calls.forEach { a.put(it) } }))
    }
    return out
}

/**
 * [Analysis] as a flat map of path -> string, so a difference names itself.
 * Doubles are printed rather than stored as numbers: NaN is a value a bad
 * answer really produces on both sides, and JSON cannot hold it.
 *
 * latencyMs is excluded: it is wall-clock time, not a value either side
 * computed. Everything else is compared.
 */
fun flattenAnalysis(a: Analysis): JSONObject {
    val m = JSONObject()
    fun choice(prefix: String, c: com.jev.probe.core.Choice?) {
        if (c == null) { m.put(prefix, "<null>"); return }
        m.put("$prefix.choice", c.choice)
        m.put("$prefix.confidence", c.confidence.toString())
        if (c.probabilities.isEmpty()) m.put("$prefix.probabilities", "<empty>")
        c.probabilities.toSortedMap().forEach { (k, v) -> m.put("$prefix.probabilities.$k", v.toString()) }
    }
    choice("trueIntent", a.trueIntent)
    a.dangerLevel.let {
        if (it == null) m.put("dangerLevel", "<null>")
        else {
            m.put("dangerLevel.score", it.score.toString())
            m.put("dangerLevel.confidence", it.confidence.toString())
            m.put("dangerLevel.maxLevel", it.maxLevel.toString())
        }
    }
    choice("sheNeeds", a.sheNeeds)
    m.put("shouldReplyNow", a.shouldReplyNow?.toString() ?: "<null>")
    choice("bestAction", a.bestAction)
    m.put("tensionResolved", a.tensionResolved?.toString() ?: "<null>")
    m.put("literalQuestion", a.literalQuestion?.toString() ?: "<null>")
    a.rankedReplies.forEachIndexed { i, r ->
        m.put("rankedReplies[$i].text", r.text)
        m.put("rankedReplies[$i].prob", r.prob.toString())
    }
    m.put("rankedReplies.count", a.rankedReplies.size.toString())
    m.put("error", a.error ?: "<null>")
    return m
}

// ---------------------------------------------------------------------------
// Comparing
// ---------------------------------------------------------------------------

fun asMap(o: JSONObject): Map<String, String> =
    o.keys().asSequence().associateWith { o.getString(it) }

fun covered(key: String, expected: List<String>): Boolean =
    expected.any { key == it || key.startsWith("$it.") || key.startsWith("$it[") }

fun compare(upFile: String, oursFile: String): Int {
    val up = JSONObject(File(upFile).readText())
    val ours = JSONObject(File(oursFile).readText())
    val width = (SCENARIOS.maxOf { it.name.length }) + 2
    println("场景".padEnd(width - 4) + "  结果   说明")
    println("-".repeat(width + 62))
    var failures = 0
    var expected = 0
    val details = ArrayList<Triple<String, String, List<String>>>()
    for (scn in SCENARIOS) {
        val a = asMap(up.getJSONObject(scn.name).getJSONObject("result"))
        val b = asMap(ours.getJSONObject(scn.name).getJSONObject("result"))
        val diffs = (a.keys + b.keys).sorted().mapNotNull { key ->
            val va = a[key] ?: "<absent>"
            val vb = b[key] ?: "<absent>"
            if (va == vb) null else "$key\n      jev-chat-jarvis: $va\n      我们: $vb"
        }
        val keys = diffs.map { it.substringBefore("\n") }
        val verdict = when {
            keys.isEmpty() -> "PASS"
            keys.all { covered(it, scn.expectDiff) } -> { expected++; "PASS*" }
            else -> { failures++; "FAIL" }
        }
        val mark = if (verdict == "FAIL")
            "没打算不同却不同了: " + keys.filterNot { covered(it, scn.expectDiff) }.joinToString(", ")
        else if (verdict == "PASS*") "刻意不同（下面逐条说明）: " + keys.joinToString(", ")
        else scn.note
        println(scn.name.padEnd(width) + verdict.padEnd(7) + mark)
        if (diffs.isNotEmpty()) details.add(Triple(scn.name, verdict, diffs))
    }
    println("-".repeat(width + 62))
    println("共 ${SCENARIOS.size} 个场景：${SCENARIOS.size - failures - expected} 完全一致，" +
        "$expected 刻意不同，$failures 失败")
    if (details.isNotEmpty()) {
        println("\n逐条对照（键 / jev-chat-jarvis / 我们）:")
        for ((name, verdict, diffs) in details) {
            println("\n  [$verdict] $name")
            diffs.forEach { println("    $it") }
        }
    }
    println("\n每个场景的接口调用（证明没打网络，也证明调用次数变了）:")
    for (scn in SCENARIOS.take(3)) {
        println("    ${scn.name}: jev-chat-jarvis ${up.getJSONObject(scn.name).getJSONArray("calls")}" +
            " / 我们 ${ours.getJSONObject(scn.name).getJSONArray("calls")}")
    }
    println("\nlatencyMs 不参与比对（墙上时间，不是任何一边算出来的值）。")
    return if (failures > 0) 1 else 0
}

// ---------------------------------------------------------------------------

fun main(args: Array<String>) {
    var side: String? = null
    var out: String? = null
    var compareArgs: List<String> = emptyList()
    var i = 0
    while (i < args.size) {
        when (args[i]) {
            "--side" -> { side = args[++i] }
            "--out" -> { out = args[++i] }
            "--compare" -> { compareArgs = listOf(args[++i], args[++i]) }
            else -> error("unknown argument ${args[i]}")
        }
        i++
    }
    if (compareArgs.isNotEmpty()) exitProcess(compare(compareArgs[0], compareArgs[1]))
    requireNotNull(side) { "--side upstream|ours, or --compare a.json b.json" }
    requireNotNull(out) { "--out <file>" }
    checkQuestionSet()
    File(out).writeText(runSide(side).toString())
    println("[$side] 写出 $out")
}

/** Both sides check the scenario data against the real question set: the same
 *  7 names, the same types. A drift in either tree fails here, not silently. */
fun checkQuestionSet() {
    val questions = com.jev.probe.jev.JevQuestions.judge()
    val kinds = questions.keys().asSequence().associateWith { questions.getJSONObject(it).getString("type") }
    check(kinds == FIELD_KIND) { "JUDGE_QUESTIONS 跟测试里的题型表对不上: $kinds" }
}

// ---------------------------------------------------------------------------
// Enough of a Context for Prefs: the settings both sides read live in memory.
// ---------------------------------------------------------------------------

class MemPrefs : SharedPreferences {
    private val map = HashMap<String, Any?>()

    inner class Ed : SharedPreferences.Editor {
        private val staged = HashMap<String, Any?>()
        private val removed = HashSet<String>()
        private var wipe = false
        override fun putString(key: String, value: String?): SharedPreferences.Editor { staged[key] = value; return this }
        override fun putBoolean(key: String, value: Boolean): SharedPreferences.Editor { staged[key] = value; return this }
        override fun putInt(key: String, value: Int): SharedPreferences.Editor { staged[key] = value; return this }
        override fun putLong(key: String, value: Long): SharedPreferences.Editor { staged[key] = value; return this }
        override fun putFloat(key: String, value: Float): SharedPreferences.Editor { staged[key] = value; return this }
        override fun putStringSet(key: String, value: Set<String>?): SharedPreferences.Editor { staged[key] = value; return this }
        override fun remove(key: String): SharedPreferences.Editor { removed.add(key); return this }
        override fun clear(): SharedPreferences.Editor { wipe = true; return this }
        override fun apply() { commit() }
        override fun commit(): Boolean {
            if (wipe) map.clear()
            removed.forEach { map.remove(it) }
            map.putAll(staged)
            return true
        }
    }

    override fun getString(key: String, defValue: String?): String? = map[key] as? String ?: defValue
    @Suppress("UNCHECKED_CAST")
    override fun getStringSet(key: String, defValue: Set<String>?): Set<String>? =
        map[key] as? Set<String> ?: defValue
    override fun getBoolean(key: String, defValue: Boolean): Boolean = map[key] as? Boolean ?: defValue
    override fun getInt(key: String, defValue: Int): Int = map[key] as? Int ?: defValue
    override fun getLong(key: String, defValue: Long): Long = map[key] as? Long ?: defValue
    override fun getFloat(key: String, defValue: Float): Float = map[key] as? Float ?: defValue
    override fun contains(key: String): Boolean = map.containsKey(key)
    override fun edit(): SharedPreferences.Editor = Ed()
}

class FakeContext : Context() {
    private val files = HashMap<String, MemPrefs>()
    override fun getSharedPreferences(name: String, mode: Int): SharedPreferences =
        files.getOrPut(name) { MemPrefs() }
}
