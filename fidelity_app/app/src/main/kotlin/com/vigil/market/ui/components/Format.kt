package com.vigil.market.ui.components

fun cents(value: Int): String = "$${"%.2f".format(value / 100.0)}"
