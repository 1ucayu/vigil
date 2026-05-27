package com.vigil.clock.ui.components

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Alarm
import androidx.compose.material.icons.filled.HourglassBottom
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Timer
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import com.vigil.clock.AppState
import com.vigil.clock.Screen

@OptIn(ExperimentalMaterial3Api::class, androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
fun AppChrome(
    state: AppState,
    content: @Composable (PaddingValues) -> Unit,
) {
    Scaffold(
        modifier = Modifier.semantics { testTagsAsResourceId = true },
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Text(
                        text = titleFor(state.screen),
                        modifier = Modifier.testTag("app.top_bar.title"),
                    )
                },
                colors = TopAppBarDefaults.centerAlignedTopAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                    titleContentColor = MaterialTheme.colorScheme.onSurface,
                ),
            )
        },
        bottomBar = { BottomNav(state) },
        containerColor = MaterialTheme.colorScheme.background,
        content = { innerPadding ->
            Surface(color = MaterialTheme.colorScheme.background) {
                content(innerPadding)
            }
        },
    )
}

@Composable
private fun BottomNav(state: AppState) {
    NavigationBar(
        modifier = Modifier.testTag("app.bottom_nav"),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        NavigationBarItem(
            selected = state.screen == Screen.ALARM_LIST || state.screen == Screen.ALARM_EDIT,
            onClick = { state.navigate(Screen.ALARM_LIST) },
            icon = { Icon(Icons.Filled.Alarm, contentDescription = "Alarms") },
            label = { Text("Alarms") },
            modifier = Modifier.testTag("nav.open_alarms"),
        )
        NavigationBarItem(
            selected = state.screen == Screen.TIMER_SETUP ||
                state.screen == Screen.TIMER_RUNNING ||
                state.screen == Screen.TIMER_PAUSED ||
                state.screen == Screen.TIMER_DONE,
            onClick = { state.navigate(Screen.TIMER_SETUP) },
            icon = { Icon(Icons.Filled.HourglassBottom, contentDescription = "Timer") },
            label = { Text("Timer") },
            modifier = Modifier.testTag("nav.open_timer"),
        )
        NavigationBarItem(
            selected = state.screen == Screen.STOPWATCH_IDLE ||
                state.screen == Screen.STOPWATCH_RUNNING ||
                state.screen == Screen.STOPWATCH_PAUSED,
            onClick = { state.navigate(Screen.STOPWATCH_IDLE) },
            icon = { Icon(Icons.Filled.Timer, contentDescription = "Stopwatch") },
            label = { Text("Stopwatch") },
            modifier = Modifier.testTag("nav.open_stopwatch"),
        )
        NavigationBarItem(
            selected = state.screen == Screen.SETTINGS,
            onClick = { state.navigate(Screen.SETTINGS) },
            icon = { Icon(Icons.Filled.Settings, contentDescription = "Settings") },
            label = { Text("Settings") },
            modifier = Modifier.testTag("nav.open_settings"),
        )
    }
}

private fun titleFor(screen: Screen): String = when (screen) {
    Screen.ALARM_LIST -> "Alarms"
    Screen.ALARM_EDIT -> "Edit alarm"
    Screen.TIMER_SETUP -> "Timer"
    Screen.TIMER_RUNNING -> "Timer"
    Screen.TIMER_PAUSED -> "Timer"
    Screen.TIMER_DONE -> "Timer"
    Screen.STOPWATCH_IDLE -> "Stopwatch"
    Screen.STOPWATCH_RUNNING -> "Stopwatch"
    Screen.STOPWATCH_PAUSED -> "Stopwatch"
    Screen.SETTINGS -> "Settings"
}
