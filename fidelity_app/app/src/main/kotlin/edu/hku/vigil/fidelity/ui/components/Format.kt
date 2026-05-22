package edu.hku.vigil.fidelity.ui.components

fun cents(value: Int): String = "$${"%.2f".format(value / 100.0)}"
