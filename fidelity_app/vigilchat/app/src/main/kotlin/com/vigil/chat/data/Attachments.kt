package com.vigil.chat.data

data class Attachment(
    val id: String,
    val name: String,
)

object Attachments {
    val all: List<Attachment> = listOf(
        Attachment("photo_beach", "Beach photo"),
        Attachment("doc_report", "Q3 report.pdf"),
        Attachment("sticker_thumbs", "Thumbs-up sticker"),
    )

    fun byId(id: String): Attachment? = all.firstOrNull { it.id == id }
}
