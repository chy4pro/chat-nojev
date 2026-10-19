package com.jev.probe.capture

import android.content.res.Resources
import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import com.jev.probe.core.ChatSnapshot
import com.jev.probe.core.Msg

/**
 * WhatsApp (com.whatsapp). The reading rules are ported from the WhatsApp reader
 * contributed by @smgonthebeat in #73, recorded on WhatsApp 2.26.38.73. They rely
 * on resource ids and positions only, never on UI wording, so the display
 * language does not matter. Left-to-right layout is assumed.
 *
 * - Only one-to-one chats are read. A group chat (sender-name or group-avatar ids
 *   present) returns null, so the service treats it as "not a chat window".
 * - Never an empty snapshot: an empty one would start the screenshot + OCR
 *   fallback, and WhatsApp is never screenshotted. No readable text → null.
 * - Sender: the bubble (`main_layout`) hugs its sender's side of the message
 *   list. A delivery-status icon exists only on the user's own messages; when it
 *   contradicts the position the row is dropped rather than guessed.
 * - Only rows carrying message text are kept (text messages, captions). Date
 *   dividers, system notices, deleted-message stubs and media without text are
 *   skipped.
 */
class WhatsAppAdapter : ChatAppAdapter {
    override val pkg = WhatsAppRules.PACKAGE

    override fun extract(root: AccessibilityNodeInfo, res: Resources): ChatSnapshot? =
        WhatsAppRules.read(A11yNode(root))?.let { ChatSnapshot(it.title, it.messages) }

    /** Live accessibility node behind the [WaNode] view the rules read. */
    private class A11yNode(private val node: AccessibilityNodeInfo) : WaNode {
        override val viewId: String? get() = node.viewIdResourceName
        override val text: String? get() = node.text?.toString()
        override val bounds: IntArray
            get() = Rect().also { node.getBoundsInScreen(it) }.let { intArrayOf(it.left, it.top, it.right, it.bottom) }
        override val isScrollable: Boolean get() = node.isScrollable
        override val childCount: Int get() = node.childCount
        override fun child(i: Int): WaNode? = node.getChild(i)?.let { A11yNode(it) }
    }
}

/** The part of a node the WhatsApp rules look at; bounds are `[left, top, right, bottom]`. */
internal interface WaNode {
    val viewId: String?
    val text: String?
    val bounds: IntArray
    val isScrollable: Boolean
    val childCount: Int
    fun child(i: Int): WaNode?
}

/** Pure reading rules, kept free of Android classes so recorded screens can drive them in unit tests. */
internal object WhatsAppRules {
    const val PACKAGE = "com.whatsapp"

    data class Read(val title: String?, val messages: List<Msg>)

    fun read(root: WaNode): Read? {
        val all = descendants(root, includeSelf = true)
        val ids = all.mapNotNullTo(HashSet()) { it.viewId }
        // Conversation page = contact name in the top bar plus the message box.
        if (ID_CONTACT_NAME !in ids || ID_ENTRY !in ids) return null
        if (GROUP_IDS.any { it in ids }) return null
        val list = all.firstOrNull { it.viewId == ID_LIST && it.isScrollable } ?: return null
        val title = all.firstOrNull { it.viewId == ID_CONTACT_NAME }?.text?.takeIf { it.isNotBlank() }

        val lb = list.bounds
        val messages = ArrayList<Msg>()
        for (i in 0 until list.childCount) {
            val row = list.child(i) ?: continue
            readRow(row, lb)?.let { messages.add(it) }
        }
        if (messages.isEmpty()) return null
        return Read(title, messages)
    }

    private fun readRow(row: WaNode, listBounds: IntArray): Msg? {
        val parts = descendants(row, includeSelf = false)
        val main = parts.firstOrNull { it.viewId == ID_MAIN_LAYOUT } ?: return null
        val body = parts.firstOrNull { it.viewId == ID_MESSAGE_TEXT }?.text?.takeIf { it.isNotBlank() } ?: return null
        // A deleted-message stub carries its notice as message text; that is UI wording, not a message.
        val typedRow = row.viewId in TYPED_ROWS
        if (!typedRow && parts.any { it.viewId == ID_ICON }) return null

        val mb = main.bounds
        val leftMargin = mb[0] - listBounds[0]
        val rightMargin = listBounds[2] - mb[2]
        val side = when {
            leftMargin < rightMargin -> "other"
            rightMargin < leftMargin -> "me"
            else -> return null
        }
        val hasStatusIcon = parts.any { it.viewId == ID_STATUS }
        if (hasStatusIcon && side == "other") return null
        return Msg(side, body)
    }

    /** Document order, bounded like the other adapters' walks. */
    private fun descendants(node: WaNode, includeSelf: Boolean): List<WaNode> {
        val out = ArrayList<WaNode>()
        val stack = ArrayDeque<WaNode>()
        if (includeSelf) stack.addLast(node)
        else for (i in node.childCount - 1 downTo 0) node.child(i)?.let { stack.addLast(it) }
        var guard = 0
        while (stack.isNotEmpty() && guard < 5000) {
            guard++
            val n = stack.removeLast()
            out.add(n)
            for (i in n.childCount - 1 downTo 0) n.child(i)?.let { stack.addLast(it) }
        }
        return out
    }

    private const val WA = "com.whatsapp:id/"
    private const val ID_LIST = "android:id/list"
    private const val ID_CONTACT_NAME = WA + "conversation_contact_name"
    private const val ID_ENTRY = WA + "entry"
    private const val ID_MAIN_LAYOUT = WA + "main_layout"
    private const val ID_MESSAGE_TEXT = WA + "message_text"
    private const val ID_STATUS = WA + "status"
    private const val ID_ICON = WA + "icon"
    private val TYPED_ROWS = setOf(
        WA + "conversation_row_text", WA + "conversation_row_image",
        WA + "conversation_row_voice_note", WA + "conversation_row_document"
    )
    /** Seen only in group chats (recorded 2026-10-03; absent from every one-to-one recording). */
    private val GROUP_IDS = listOf(
        WA + "name_in_group", WA + "name_in_group_tv",
        WA + "conversation_row_name_in_group_name_and_role_container",
        WA + "group_profile_initials", WA + "groupPhotoCameraIcon"
    )
}
