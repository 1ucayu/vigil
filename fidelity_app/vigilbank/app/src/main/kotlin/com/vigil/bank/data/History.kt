package com.vigil.bank.data

data class HistoryEntry(
    val id: String,
    val recipientName: String,
    val amountCents: Int,
    val memo: String,
)

object History {
    /** Two fixed prior transfers. No timestamps; deterministic strings only. */
    val seed: List<HistoryEntry> = listOf(
        HistoryEntry("T-001", "Bob Singh",  2500, "Lunch"),
        HistoryEntry("T-002", "Carol Diaz", 4000, "Books"),
    )
}
