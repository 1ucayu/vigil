package com.vigil.clock.data

data class Alarm(
    val id: String,
    val hour: Int,
    val minute: Int,
    val enabled: Boolean,
    val label: String,
)

object Alarms {
    /** Deterministic seed alarms used to populate AppState on launch / reset. */
    val seed: List<Alarm> = listOf(
        Alarm("a_morning", 7, 0, true, "Morning"),
        Alarm("a_lunch", 12, 30, false, "Lunch"),
        Alarm("a_evening", 18, 15, true, "Evening"),
    )
}
