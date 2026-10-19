package com.jev.probe.capture

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Drives [WhatsAppRules] with WhatsApp 2.26.38.73 screens recorded by
 * @smgonthebeat (#73) from a test chat between two test accounts. The expected
 * files come from that reader's own golden output, filtered to what this app
 * keeps: messages with text whose sender is known.
 */
class WhatsAppRulesTest {

    @Test fun conversationScreensMatchTheRecordedReading() {
        for (n in listOf(1, 2, 3, 4, 5, 6, 7)) {
            val name = "dump-%04d".format(n)
            val read = WhatsAppRules.read(load(name))
            assertNotNull("$name should read as a one-to-one conversation", read)
            val got = read!!.messages.map { "${it.side}\t${it.text}" }
            assertEquals("$name messages", expected(name), got)
        }
    }

    @Test fun chatListIsNotAConversation() {
        assertNull(WhatsAppRules.read(load("dump-0008")))
    }

    @Test fun groupChatIsNeverRead() {
        val root = load("dump-0004")
        val list = find(root) { it.viewId == "android:id/list" }!!
        val firstRow = list.kids.first()
        firstRow.kids.add(FakeNode("com.whatsapp:id/name_in_group", "Alex", intArrayOf(0, 0, 10, 10), false))
        assertNull(WhatsAppRules.read(root))
    }

    @Test fun maskedTitleStaysUnknown() {
        assertNull(WhatsAppRules.read(load("dump-0004"))!!.title)
    }

    // --- fixtures -----------------------------------------------------------

    private class FakeNode(
        override val viewId: String?,
        override val text: String?,
        override val bounds: IntArray,
        override val isScrollable: Boolean
    ) : WaNode {
        val kids = ArrayList<FakeNode>()
        override val childCount: Int get() = kids.size
        override fun child(i: Int): WaNode? = kids.getOrNull(i)
    }

    private fun find(node: FakeNode, pred: (FakeNode) -> Boolean): FakeNode? {
        if (pred(node)) return node
        for (k in node.kids) find(k, pred)?.let { return it }
        return null
    }

    private fun resource(path: String): List<String> {
        val stream = javaClass.classLoader!!.getResourceAsStream("whatsapp/$path")
            ?: error("missing test resource whatsapp/$path")
        return stream.bufferedReader(Charsets.UTF_8).readLines()
    }

    private fun unescape(s: String): String {
        val sb = StringBuilder()
        var i = 0
        while (i < s.length) {
            val c = s[i]
            if (c == '\\' && i + 1 < s.length) {
                when (s[i + 1]) {
                    't' -> sb.append('\t'); 'n' -> sb.append('\n'); '\\' -> sb.append('\\')
                    else -> sb.append(s[i + 1])
                }
                i += 2
            } else { sb.append(c); i++ }
        }
        return sb.toString()
    }

    private fun load(name: String): FakeNode {
        data class Row(val index: Int, val parent: Int, val childIndex: Int, val node: FakeNode)
        val rows = resource("$name.tsv").filter { it.isNotEmpty() && !it.startsWith("#") }.map { line ->
            val f = line.split('\t')
            val b = f[4].split(',').map { it.toInt() }.toIntArray()
            Row(f[0].toInt(), f[1].toInt(), f[2].toInt(),
                FakeNode(f[3].ifEmpty { null }, unescape(f[6]).ifEmpty { null }, b, f[5] == "1"))
        }
        val byIndex = rows.associateBy { it.index }
        rows.groupBy { it.parent }.forEach { (parent, children) ->
            val p = byIndex[parent] ?: return@forEach
            children.sortedBy { it.childIndex }.forEach { p.node.kids.add(it.node) }
        }
        val roots = rows.filter { it.parent !in byIndex }
        assertEquals("$name should have one root", 1, roots.size)
        return roots.single().node
    }

    private fun expected(name: String): List<String> =
        resource("$name.expected").filter { it.isNotEmpty() && !it.startsWith("#") }.map { line ->
            val tab = line.indexOf('\t')
            line.substring(0, tab) + "\t" + unescape(line.substring(tab + 1))
        }
}
