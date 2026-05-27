package com.vigil.clock

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.vigil.clock.ui.AlarmEditScreen
import com.vigil.clock.ui.AlarmListScreen
import com.vigil.clock.ui.SettingsScreen
import com.vigil.clock.ui.StopwatchIdleScreen
import com.vigil.clock.ui.StopwatchPausedScreen
import com.vigil.clock.ui.StopwatchRunningScreen
import com.vigil.clock.ui.TimerDoneScreen
import com.vigil.clock.ui.TimerPausedScreen
import com.vigil.clock.ui.TimerRunningScreen
import com.vigil.clock.ui.TimerSetupScreen
import com.vigil.clock.ui.components.AppChrome
import com.vigil.clock.ui.theme.VigilClockTheme
import kotlinx.coroutines.delay

private const val TICK_MS = 100L

@Composable
fun VigilClockApp() {
    val state = remember { AppState() }
    val ctx = LocalContext.current

    // Relative-only monotonic tick driver for the timer. `delay` is a coroutine
    // relative delay, not a wall-clock read.
    LaunchedEffect(state.screen) {
        while (state.screen == Screen.TIMER_RUNNING) {
            delay(TICK_MS)
            state.tickTimer(TICK_MS)
        }
    }
    LaunchedEffect(state.screen) {
        while (state.screen == Screen.STOPWATCH_RUNNING) {
            delay(TICK_MS)
            state.tickStopwatch(TICK_MS)
        }
    }

    VigilClockTheme {
        AppChrome(state = state) { innerPadding ->
            BackHandler(enabled = true) {
                val consumed = state.handleBack()
                if (!consumed) {
                    (ctx as? android.app.Activity)?.finish()
                }
            }

            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding),
            ) {
                when (state.screen) {
                    Screen.ALARM_LIST        -> AlarmListScreen(state)
                    Screen.ALARM_EDIT        -> AlarmEditScreen(state)
                    Screen.TIMER_SETUP       -> TimerSetupScreen(state)
                    Screen.TIMER_RUNNING     -> TimerRunningScreen(state)
                    Screen.TIMER_PAUSED      -> TimerPausedScreen(state)
                    Screen.TIMER_DONE        -> TimerDoneScreen(state)
                    Screen.STOPWATCH_IDLE    -> StopwatchIdleScreen(state)
                    Screen.STOPWATCH_RUNNING -> StopwatchRunningScreen(state)
                    Screen.STOPWATCH_PAUSED  -> StopwatchPausedScreen(state)
                    Screen.SETTINGS          -> SettingsScreen(state)
                }
            }
        }
    }
}
