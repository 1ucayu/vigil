package com.vigil.clock

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateList
import com.vigil.clock.data.Alarm
import com.vigil.clock.data.Alarms
import com.vigil.clock.data.TimerDurations

enum class Screen(val id: String) {
    ALARM_LIST("alarm_list"),
    ALARM_EDIT("alarm_edit"),
    TIMER_SETUP("timer_setup"),
    TIMER_RUNNING("timer_running"),
    TIMER_PAUSED("timer_paused"),
    TIMER_DONE("timer_done"),
    STOPWATCH_IDLE("stopwatch_idle"),
    STOPWATCH_RUNNING("stopwatch_running"),
    STOPWATCH_PAUSED("stopwatch_paused"),
    SETTINGS("settings"),
}

/**
 * Single source of truth for the FSM. All mutation funnels through methods named
 * after their canonical action; no wall-clock reads anywhere.
 *
 * The tick source is a relative monotonic counter (timerElapsedMs / stopwatchElapsedMs)
 * advanced by `tickTimer()` and `tickStopwatch()` from a `LaunchedEffect(delay=100ms)`
 * loop that VigilClockApp owns. The counter resets to 0 on every transition into a
 * RUNNING state and freezes otherwise.
 */
class AppState {
    var screen by mutableStateOf(Screen.ALARM_LIST)
        private set

    val alarms: SnapshotStateList<Alarm> = mutableStateListOf<Alarm>().apply {
        addAll(Alarms.seed)
    }

    var editingAlarmId by mutableStateOf<String?>(null)
        private set
    var editingHour by mutableStateOf(0)
        private set
    var editingMinute by mutableStateOf(0)
        private set

    var timerSelectedDurationId by mutableStateOf<String?>(null)
        private set
    var timerDurationMs by mutableStateOf(0L)
        private set
    var timerElapsedMs by mutableStateOf(0L)
        private set

    var stopwatchElapsedMs by mutableStateOf(0L)
        private set
    val stopwatchLaps: SnapshotStateList<Long> = mutableStateListOf()

    /**
     * Bottom-nav navigation. Returns to whichever sub-screen of the chosen tab is
     * current, so an in-progress timer or stopwatch is not silently reset by a tab
     * tap. See gold/fsm.json `global_navigation`.
     */
    fun navigate(target: Screen) {
        screen = when (target) {
            Screen.ALARM_LIST -> Screen.ALARM_LIST
            Screen.SETTINGS -> Screen.SETTINGS
            Screen.TIMER_SETUP -> when (screen) {
                Screen.TIMER_RUNNING, Screen.TIMER_PAUSED, Screen.TIMER_DONE -> screen
                else -> Screen.TIMER_SETUP
            }
            Screen.STOPWATCH_IDLE -> when (screen) {
                Screen.STOPWATCH_RUNNING, Screen.STOPWATCH_PAUSED -> screen
                else -> Screen.STOPWATCH_IDLE
            }
            else -> target
        }
    }

    // ----- Alarms ---------------------------------------------------------

    fun toggleAlarm(alarmId: String) {
        val idx = alarms.indexOfFirst { it.id == alarmId }
        if (idx < 0) return
        val a = alarms[idx]
        alarms[idx] = a.copy(enabled = !a.enabled)
    }

    fun startEditAlarm(alarmId: String) {
        val a = alarms.firstOrNull { it.id == alarmId } ?: return
        editingAlarmId = a.id
        editingHour = a.hour
        editingMinute = a.minute
        screen = Screen.ALARM_EDIT
    }

    fun hourInc() { editingHour = (editingHour + 1) % 24 }
    fun hourDec() { editingHour = (editingHour + 23) % 24 }
    fun minInc() { editingMinute = (editingMinute + 1) % 60 }
    fun minDec() { editingMinute = (editingMinute + 59) % 60 }

    fun saveAlarm() {
        val aid = editingAlarmId ?: return
        val idx = alarms.indexOfFirst { it.id == aid }
        if (idx >= 0) {
            alarms[idx] = alarms[idx].copy(hour = editingHour, minute = editingMinute)
        }
        editingAlarmId = null
        screen = Screen.ALARM_LIST
    }

    fun cancelAlarm() {
        editingAlarmId = null
        screen = Screen.ALARM_LIST
    }

    // ----- Timer ----------------------------------------------------------

    fun selectDuration(durationId: String) {
        val d = TimerDurations.byId(durationId) ?: return
        timerSelectedDurationId = d.id
        timerDurationMs = d.ms
    }

    fun startTimer() {
        if (timerSelectedDurationId == null) return
        if (timerDurationMs <= 0L) return
        timerElapsedMs = 0L
        screen = Screen.TIMER_RUNNING
    }

    fun pauseTimer() {
        if (screen == Screen.TIMER_RUNNING) screen = Screen.TIMER_PAUSED
    }

    fun resumeTimer() {
        if (screen == Screen.TIMER_PAUSED) screen = Screen.TIMER_RUNNING
    }

    fun resetTimer() {
        timerElapsedMs = 0L
        timerSelectedDurationId = null
        timerDurationMs = 0L
        screen = Screen.TIMER_SETUP
    }

    fun fastForwardDone() {
        if (screen == Screen.TIMER_RUNNING || screen == Screen.TIMER_PAUSED) {
            timerElapsedMs = timerDurationMs
            screen = Screen.TIMER_DONE
        }
    }

    /** Relative-only monotonic tick driven by `delay(100L)`. No wall-clock. */
    fun tickTimer(deltaMs: Long) {
        if (screen != Screen.TIMER_RUNNING) return
        val next = timerElapsedMs + deltaMs
        if (next >= timerDurationMs) {
            timerElapsedMs = timerDurationMs
            screen = Screen.TIMER_DONE
        } else {
            timerElapsedMs = next
        }
    }

    fun timerRemainingMs(): Long = (timerDurationMs - timerElapsedMs).coerceAtLeast(0L)

    // ----- Stopwatch ------------------------------------------------------

    fun startSw() {
        stopwatchElapsedMs = 0L
        stopwatchLaps.clear()
        screen = Screen.STOPWATCH_RUNNING
    }

    fun pauseSw() {
        if (screen == Screen.STOPWATCH_RUNNING) screen = Screen.STOPWATCH_PAUSED
    }

    fun resumeSw() {
        if (screen == Screen.STOPWATCH_PAUSED) screen = Screen.STOPWATCH_RUNNING
    }

    fun lap() {
        if (screen == Screen.STOPWATCH_RUNNING) {
            stopwatchLaps.add(stopwatchElapsedMs)
        }
    }

    fun resetSw() {
        stopwatchElapsedMs = 0L
        stopwatchLaps.clear()
        screen = Screen.STOPWATCH_IDLE
    }

    /** Relative-only monotonic tick driven by `delay(100L)`. No wall-clock. */
    fun tickStopwatch(deltaMs: Long) {
        if (screen != Screen.STOPWATCH_RUNNING) return
        stopwatchElapsedMs += deltaMs
    }

    // ----- Settings -------------------------------------------------------

    /**
     * Full reset. A fresh launch and an in-app reset produce identical UI
     * fingerprints.
     */
    fun resetDemo() {
        screen = Screen.ALARM_LIST
        alarms.clear()
        alarms.addAll(Alarms.seed)
        editingAlarmId = null
        editingHour = 0
        editingMinute = 0
        timerSelectedDurationId = null
        timerDurationMs = 0L
        timerElapsedMs = 0L
        stopwatchElapsedMs = 0L
        stopwatchLaps.clear()
    }

    /**
     * Deterministic system-back routing. Returns true if the back press was consumed
     * (caller stays in-app); false means the host activity should exit to launcher.
     *
     * Only ALARM_EDIT pops back to its parent (ALARM_LIST). Every other top-level
     * tab returns false, so back from a running timer or stopwatch exits to the
     * launcher without altering the monotonic counter.
     */
    fun handleBack(): Boolean {
        if (screen == Screen.ALARM_EDIT) {
            editingAlarmId = null
            screen = Screen.ALARM_LIST
            return true
        }
        return false
    }
}
