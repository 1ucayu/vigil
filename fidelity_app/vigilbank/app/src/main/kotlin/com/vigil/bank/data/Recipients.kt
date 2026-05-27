package com.vigil.bank.data

data class Recipient(
    val id: String,
    val displayName: String,
    val accountMasked: String,
)

object Recipients {
    val all: List<Recipient> = listOf(
        Recipient("alice", "Alice Chen",  "•••• 4821"),
        Recipient("bob",   "Bob Singh",   "•••• 7204"),
        Recipient("carol", "Carol Diaz",  "•••• 9015"),
    )

    fun byId(id: String): Recipient? = all.firstOrNull { it.id == id }
}
