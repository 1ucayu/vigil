package com.vigil.bank.ui.components

/** Format an integer cent amount as "$X.YZ". */
fun cents(value: Int): String = "$${"%.2f".format(value / 100.0)}"
