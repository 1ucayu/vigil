package com.vigil.clock.ui.components

/**
 * Deterministic, pure formatting for elapsed/remaining durations.
 *
 * Uses only relative-millisecond arithmetic; no wall-clock reads.
 *
 * - Below one hour: "mm:ss.t" (e.g. "02:07.4")
 * - One hour and above: "hh:mm:ss" (e.g. "01:23:45")
 *
 * Negative inputs are clamped to zero so the UI never shows -00:00.x.
 */
fun formatMs(ms: Long): String {
    val clamped = if (ms < 0L) 0L else ms
    val totalSeconds = clamped / 1000L
    val tenths = (clamped % 1000L) / 100L
    val hours = totalSeconds / 3600L
    val minutes = (totalSeconds % 3600L) / 60L
    val seconds = totalSeconds % 60L
    return if (hours > 0L) {
        "%02d:%02d:%02d".format(hours, minutes, seconds)
    } else {
        "%02d:%02d.%d".format(minutes, seconds, tenths)
    }
}
