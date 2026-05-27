package com.vigil.clock.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.clock.AppState
import com.vigil.clock.data.TimerDurations
import com.vigil.clock.ui.components.ScreenMarker

@Composable
fun TimerSetupScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        ScreenMarker("timer_setup")
        Text(
            "Pick a duration",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("timer_setup.title"),
        )

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TimerDurations.all.forEach { d ->
                FilterChip(
                    selected = state.timerSelectedDurationId == d.id,
                    onClick = { state.selectDuration(d.id) },
                    label = { Text(d.label) },
                    modifier = Modifier.testTag("timer.duration.${d.id}"),
                )
            }
        }
        Text(
            text = state.timerSelectedDurationId ?: "",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag("timer.selected_duration_id"),
        )

        Button(
            onClick = { state.startTimer() },
            enabled = state.timerSelectedDurationId != null,
            modifier = Modifier.testTag("timer.start"),
        ) { Text("Start") }
    }
}
