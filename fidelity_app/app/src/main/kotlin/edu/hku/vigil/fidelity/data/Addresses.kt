package edu.hku.vigil.fidelity.data

data class Address(
    val id: String,
    val label: String,
    val line: String,
)

object Addresses {
    val all: List<Address> = listOf(
        Address("home",   "Home",   "123 Main St"),
        Address("office", "Office", "88 King Rd"),
        Address("gym",    "Gym",    "5 Park Ave"),
    )

    fun byId(id: String): Address? = all.firstOrNull { it.id == id }
}
