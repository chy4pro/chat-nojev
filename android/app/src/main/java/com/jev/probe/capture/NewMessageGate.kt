package com.jev.probe.capture

import com.jev.probe.core.Msg

/**
 * Decides whether a chat screen brings anything new. Scrolling through history
 * changes which messages are on screen without anything new arriving, so it must
 * neither re-analyze nor clear the card.
 *
 * A message counts as new only when unseen messages sit BELOW one that was seen
 * before: that is what arriving at the bottom of a chat looks like. A fling into
 * old history shows nothing but unseen messages with no seen one above them, so
 * it is not new either.
 *
 * Messages are keyed by side + text, so a repeated identical text from the same
 * side reads as already seen. Call [reset] whenever the conversation changes.
 */
internal class NewMessageGate {

    enum class Verdict { FIRST_LOOK, NEW_FROM_OTHER, NEW_FROM_ME, NOTHING_NEW }

    private val seen = LinkedHashSet<String>()

    fun reset() = seen.clear()

    fun observe(messages: List<Msg>): Verdict {
        if (messages.isEmpty()) return Verdict.NOTHING_NEW
        val first = seen.isEmpty()
        val keys = messages.map { key(it) }
        val lastSeen = keys.indexOfLast { it in seen }
        keys.forEach { seen.remove(it); seen.add(it) }
        while (seen.size > MAX_KEYS) seen.remove(seen.first())
        return when {
            first -> Verdict.FIRST_LOOK
            lastSeen < 0 || lastSeen == keys.lastIndex -> Verdict.NOTHING_NEW
            messages.last().side == "other" -> Verdict.NEW_FROM_OTHER
            else -> Verdict.NEW_FROM_ME
        }
    }

    private fun key(m: Msg) = m.side + "\u0000" + m.text

    private companion object {
        const val MAX_KEYS = 500
    }
}
