package com.vigil.chat.data

data class Contact(
    val id: String,
    val name: String,
)

object Contacts {
    val all: List<Contact> = listOf(
        Contact("alice", "Alice Chen"),
        Contact("bob", "Bob Singh"),
        Contact("carol", "Carol Diaz"),
        Contact("dad", "Dad"),
    )

    fun nameOf(id: String): String? = all.firstOrNull { it.id == id }?.name
}
