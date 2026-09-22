package com.jev.probe.jev

import org.json.JSONObject

/**
 * The network seam for tools/equivalence_check_android.kt, and nothing else.
 *
 * Both trees are compiled for the differential test with their own
 * `jev/HttpJson.kt` left out and this file put in its place, so every client
 * runs its real code — its prompt, its request body, its parser, its
 * translation — over canned responses and never opens a socket. [Route] and
 * [ApiException] are copied from the real file verbatim; only [HttpJson.post]
 * is replaced.
 *
 * Never goes near the app or an APK.
 */
object Route {
    const val JUDGE = "判断接口"
    const val REPLY = "回复接口"
    const val VISION = "视觉接口"
}

class ApiException(
    val route: String,
    val status: Int?,
    val snippet: String
) : RuntimeException(buildMessage(route, status, snippet)) {

    companion object {
        fun buildMessage(route: String, status: Int?, snippet: String): String =
            if (status != null) "$route HTTP $status：${snippet.take(120)}"
            else "$route 请求失败：${snippet.take(120)}"
    }
}

object HttpJson {

    /** Queued responses per route, served in the order they were enqueued. */
    private val queued = HashMap<String, ArrayDeque<JSONObject>>()

    /** Routes posted to, in order, for the current scenario. Printed by the
     *  check as evidence of how many calls each side makes. */
    val calls = ArrayList<String>()

    fun reset() {
        queued.clear()
        calls.clear()
    }

    fun enqueue(route: String, response: JSONObject) {
        queued.getOrPut(route) { ArrayDeque() }.addLast(response)
    }

    fun post(
        url: String,
        key: String,
        body: JSONObject,
        route: String,
        extraHeaders: Map<String, String> = emptyMap()
    ): JSONObject {
        calls.add(route)
        val q = queued[route]
        if (q == null || q.isEmpty()) throw ApiException(route, null, "stub: no queued response")
        return q.removeFirst()
    }

    fun headersFor(url: String): Map<String, String> = emptyMap()
}
