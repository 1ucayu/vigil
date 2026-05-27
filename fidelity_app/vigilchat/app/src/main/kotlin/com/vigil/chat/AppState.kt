package com.vigil.chat

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.compose.runtime.snapshots.SnapshotStateMap
import androidx.compose.runtime.toMutableStateList
import com.vigil.chat.data.Attachment
import com.vigil.chat.data.Contact
import com.vigil.chat.data.Threads

/**
 * All FSM-visible screens. Dialog states are explicit FSM members and are entered
 * by setting [AppState.activeDialog]. The single-authority screen_marker rule
 * applies: at any moment exactly one ScreenMarker node is mounted.
 */
enum class Screen(val id: String) {
    INBOX("inbox"),
    THREAD("thread"),
    ATTACHMENT_PICKER("attachment_picker"),
    CONTACTS("contacts"),
    SETTINGS("settings"),
}

/** Anchored dialog states overlaid on top of the base screen. */
enum class Dialog(val stateId: String) {
    MESSAGE_OPTIONS("message_options"),
    DELETE_CONFIRM("delete_confirm"),
}

data class Message(
    val id: String,
    val threadId: String,
    val body: String,
    val outgoing: Boolean,
)

class AppState {
    var screen by mutableStateOf(Screen.INBOX)
        private set

    var currentThreadId by mutableStateOf<String?>(null)
        private set

    var messageDraft by mutableStateOf("")
        private set

    var selectedMessageId by mutableStateOf<String?>(null)
        private set

    var activeDialog by mutableStateOf<Dialog?>(null)
        private set

    /** Per-thread message lists. Compose-observable. */
    val messages: SnapshotStateMap<String, SnapshotStateList<Message>> = mutableStateMapOf()

    /**
     * Monotonic per-thread message-id counter. Deleting a message does NOT
     * decrement this; ids are never re-issued in the lifetime of the demo
     * (a [resetDemo] resets it back to the seed values).
     */
    val threadSeqs: SnapshotStateMap<String, Int> = mutableStateMapOf()

    init {
        loadSeed()
    }

    private fun loadSeed() {
        messages.clear()
        threadSeqs.clear()
        for ((tid, seed) in Threads.seedMessages) {
            messages[tid] = seed.toMutableStateList()
            threadSeqs[tid] = seed.size + 1
        }
    }

    // ---- inbox / nav ----

    fun navigate(target: Screen) {
        screen = target
    }

    fun openThread(threadId: String) {
        ensureThread(threadId)
        currentThreadId = threadId
        messageDraft = ""
        screen = Screen.THREAD
    }

    fun openContact(contactId: String) {
        // Contacts open a thread keyed by contactId. If no thread exists, create one.
        openThread(contactId)
    }

    private fun ensureThread(threadId: String) {
        if (messages[threadId] == null) {
            messages[threadId] = mutableListOf<Message>().toMutableStateList()
            threadSeqs[threadId] = 1
        }
    }

    // ---- compose / send ----

    fun setDraft(s: String) {
        messageDraft = s
    }

    fun sendMessage() {
        val tid = currentThreadId ?: return
        val draft = messageDraft
        if (draft.isBlank()) return
        appendMessage(tid, draft, outgoing = true)
        messageDraft = ""
    }

    private fun appendMessage(threadId: String, body: String, outgoing: Boolean) {
        ensureThread(threadId)
        val seq = threadSeqs[threadId] ?: 1
        val id = "m_${threadId}_$seq"
        threadSeqs[threadId] = seq + 1
        messages[threadId]?.add(Message(id = id, threadId = threadId, body = body, outgoing = outgoing))
    }

    // ---- attachments ----

    fun openAttachmentPicker() {
        if (currentThreadId == null) return
        screen = Screen.ATTACHMENT_PICKER
    }

    fun pickAttachment(attachment: Attachment) {
        val tid = currentThreadId ?: return
        appendMessage(tid, "[attachment:${attachment.name}]", outgoing = true)
        screen = Screen.THREAD
    }

    fun cancelAttachment() {
        screen = Screen.THREAD
    }

    // ---- message options / delete dialogs ----

    fun openMessageOptions(messageId: String) {
        selectedMessageId = messageId
        activeDialog = Dialog.MESSAGE_OPTIONS
    }

    fun closeMessageOptions() {
        activeDialog = null
        selectedMessageId = null
    }

    fun openDeleteConfirm() {
        activeDialog = Dialog.DELETE_CONFIRM
    }

    fun cancelDelete() {
        // Back to options dialog (per spec).
        activeDialog = Dialog.MESSAGE_OPTIONS
    }

    fun confirmDelete() {
        val tid = currentThreadId
        val mid = selectedMessageId
        if (tid != null && mid != null) {
            messages[tid]?.removeAll { it.id == mid }
            // Counter is monotonic: do NOT decrement threadSeqs.
        }
        selectedMessageId = null
        activeDialog = null
    }

    // ---- settings ----

    /**
     * Resets every piece of FSM-visible state so a fresh launch and an in-app
     * reset produce identical fingerprints.
     */
    fun resetDemo() {
        screen = Screen.INBOX
        currentThreadId = null
        messageDraft = ""
        selectedMessageId = null
        activeDialog = null
        loadSeed()
    }

    // ---- back ----

    /** Deterministic system-back routing. Returns true if the back press was consumed. */
    fun handleBack(): Boolean {
        when (activeDialog) {
            Dialog.DELETE_CONFIRM -> { activeDialog = Dialog.MESSAGE_OPTIONS; return true }
            Dialog.MESSAGE_OPTIONS -> { activeDialog = null; selectedMessageId = null; return true }
            null -> { /* fall through */ }
        }
        return when (screen) {
            Screen.INBOX -> false
            Screen.THREAD -> {
                currentThreadId = null
                messageDraft = ""
                screen = Screen.INBOX
                true
            }
            Screen.ATTACHMENT_PICKER -> { screen = Screen.THREAD; true }
            Screen.CONTACTS -> { screen = Screen.INBOX; true }
            Screen.SETTINGS -> { screen = Screen.INBOX; true }
        }
    }

    // ---- read helpers ----

    fun currentMessages(): List<Message> {
        val tid = currentThreadId ?: return emptyList()
        return messages[tid] ?: emptyList()
    }

    fun currentThreadTitle(): String {
        val tid = currentThreadId ?: return ""
        return Threads.titleOf(tid) ?: tid
    }
}
