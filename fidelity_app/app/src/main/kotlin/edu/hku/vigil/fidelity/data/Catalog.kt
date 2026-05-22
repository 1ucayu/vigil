package edu.hku.vigil.fidelity.data

data class Product(
    val id: String,
    val name: String,
    val priceCents: Int,
    val description: String,
)

object Catalog {
    val products: List<Product> = listOf(
        Product("espresso",   "Espresso",   450, "Bold single shot."),
        Product("green_tea",  "Green Tea",  300, "Steeped sencha."),
        Product("latte",      "Latte",      500, "Espresso with steamed milk."),
        Product("mocha",      "Mocha",      550, "Chocolate latte."),
        Product("oat_cookie", "Oat Cookie", 275, "House-baked oat cookie."),
    )

    fun byId(id: String): Product? = products.firstOrNull { it.id == id }

    fun search(query: String): List<Product> {
        if (query.isBlank()) return emptyList()
        val q = query.trim().lowercase()
        return products.filter { it.name.lowercase().contains(q) || it.id.contains(q) }
    }
}
