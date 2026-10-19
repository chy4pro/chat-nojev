package com.jev.probe.capture

import com.jev.probe.capture.NewMessageGate.Verdict
import com.jev.probe.core.Msg
import org.junit.Assert.assertEquals
import org.junit.Test

class NewMessageGateTest {

    private fun o(t: String) = Msg("other", t)
    private fun m(t: String) = Msg("me", t)

    private val history = listOf(o("a"), m("b"), o("c"), m("d"), o("e"), m("f"), o("g"), m("h"))

    @Test fun firstScreenOfAChatIsAFirstLook() {
        val gate = NewMessageGate()
        assertEquals(Verdict.FIRST_LOOK, gate.observe(history.takeLast(4)))
    }

    @Test fun scrollingUpAndBackIsNothingNew() {
        val gate = NewMessageGate()
        gate.observe(history.subList(4, 8))          // e f g h (bottom)
        assertEquals(Verdict.NOTHING_NEW, gate.observe(history.subList(2, 6)))   // scrolled up: c d e f
        assertEquals(Verdict.NOTHING_NEW, gate.observe(history.subList(0, 4)))   // further: a b c d
        assertEquals(Verdict.NOTHING_NEW, gate.observe(history.subList(4, 8)))   // back at the bottom
    }

    @Test fun flingingIntoUnseenHistoryIsNothingNew() {
        val gate = NewMessageGate()
        gate.observe(history.subList(6, 8))          // g h
        assertEquals(Verdict.NOTHING_NEW, gate.observe(history.subList(0, 3)))   // a b c, none seen before
    }

    @Test fun newIncomingMessageAtTheBottomIsNewFromOther() {
        val gate = NewMessageGate()
        gate.observe(history.subList(4, 8))
        assertEquals(Verdict.NEW_FROM_OTHER, gate.observe(history.subList(5, 8) + o("i")))
    }

    @Test fun twoIncomingMessagesAtOnceStillCount() {
        val gate = NewMessageGate()
        gate.observe(history.subList(4, 8))
        assertEquals(Verdict.NEW_FROM_OTHER, gate.observe(history.subList(6, 8) + o("i") + o("j")))
    }

    @Test fun ownNewMessageIsNewFromMe() {
        val gate = NewMessageGate()
        gate.observe(history.subList(4, 8))
        assertEquals(Verdict.NEW_FROM_ME, gate.observe(history.subList(5, 8) + m("i")))
    }

    @Test fun resetStartsOver() {
        val gate = NewMessageGate()
        gate.observe(history.subList(4, 8))
        gate.reset()
        assertEquals(Verdict.FIRST_LOOK, gate.observe(history.subList(4, 8)))
    }
}
