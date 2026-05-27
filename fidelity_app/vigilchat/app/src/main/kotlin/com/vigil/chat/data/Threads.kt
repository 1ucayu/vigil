package com.vigil.chat.data

/** Seeded threads, keyed by stable thread id. */
object Threads {
    /** Stable ordering for the inbox list. */
    val order: List<String> = listOf("alice", "bob", "dad")

    private val titles: Map<String, String> = mapOf(
        "alice" to "Alice Chen",
        "bob" to "Bob Singh",
        "dad" to "Dad",
    )

    fun titleOf(threadId: String): String? = titles[threadId] ?: Contacts.nameOf(threadId)

    /** Seed messages. Body strings are stable; no timestamps. */
    val seedMessages: Map<String, List<com.vigil.chat.Message>> = linkedMapOf(
        "alice" to listOf(
            com.vigil.chat.Message(
                id = "m_alice_1",
                threadId = "alice",
                body = "Hey, are you free tonight?",
                outgoing = false,
            ),
            com.vigil.chat.Message(
                id = "m_alice_2",
                threadId = "alice",
                body = "Yes! Where?",
                outgoing = true,
            ),
        ),
        "bob" to listOf(
            com.vigil.chat.Message(
                id = "m_bob_1",
                threadId = "bob",
                body = "Did you push the patch?",
                outgoing = false,
            ),
            com.vigil.chat.Message(
                id = "m_bob_2",
                threadId = "bob",
                body = "Merging now.",
                outgoing = true,
            ),
        ),
        "dad" to listOf(
            com.vigil.chat.Message(
                id = "m_dad_1",
                threadId = "dad",
                body = "Call me when you can.",
                outgoing = false,
            ),
            com.vigil.chat.Message(
                id = "m_dad_2",
                threadId = "dad",
                body = "Will do.",
                outgoing = true,
            ),
        ),
    )

    /** Last-message preview for the inbox row. */
    fun previewOf(threadId: String): String =
        seedMessages[threadId]?.lastOrNull()?.body ?: ""
}
