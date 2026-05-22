package com.vigil.market.data

data class PastOrder(
    val id: String,
    val productName: String,
    val qty: Int,
    val totalCents: Int,
    val addressLabel: String,
)

object Orders {
    val past: List<PastOrder> = listOf(
        PastOrder("ORD-1001", "Latte",     1,  500, "Home"),
        PastOrder("ORD-1002", "Green Tea", 2,  600, "Office"),
    )
}
