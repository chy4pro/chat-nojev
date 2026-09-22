package com.jev.probe.jev

import com.jev.probe.core.Analysis
import com.jev.probe.core.ChatSnapshot
import com.jev.probe.core.Prefs
import com.jev.probe.core.kb.ChatContext

/**
 * Thin facade over the model clients so callers keep one entry point.
 * Construct with [Prefs] — every route reads its own address / key / model from
 * there, so switching providers in settings takes effect on the next call.
 *
 * Upstream had two analysis calls behind this facade: `judge` on the judgment
 * route and `draftAndRank` on the generative one, run in parallel by the
 * capture service. There is one now, so there is one method.
 */
class JevClient(prefs: Prefs) {

    private val replyClient = ReplyClient(prefs)

    /**
     * The whole analysis: judgment fields, candidate replies and their ranking
     * from a single call. Errors come back inside [Analysis.error].
     */
    fun analyze(snapshot: ChatSnapshot, relationship: String, ctx: ChatContext? = null): Analysis =
        replyClient.draftAndJudge(snapshot, relationship, ctx)
}
