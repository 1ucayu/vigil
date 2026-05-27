package com.vigil.clock.data

data class TimerDuration(
    val id: String,
    val label: String,
    val ms: Long,
)

object TimerDurations {
    /** Stable preset chips offered on the TIMER_SETUP screen. */
    val all: List<TimerDuration> = listOf(
        TimerDuration("d_1m", "1m", 60_000L),
        TimerDuration("d_5m", "5m", 300_000L),
        TimerDuration("d_10m", "10m", 600_000L),
        TimerDuration("d_25m", "25m", 1_500_000L),
    )

    fun byId(id: String): TimerDuration? = all.firstOrNull { it.id == id }
}
