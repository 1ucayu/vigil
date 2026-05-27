package com.vigil.clock.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.clock.AppState
import com.vigil.clock.ui.components.ScreenMarker
import com.vigil.clock.ui.components.formatMs

@Composable
fun TimerPausedScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        ScreenMarker("timer_paused")
        Text(
            "Paused",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("timer_paused.title"),
        )
        Text(
            formatMs(state.timerRemainingMs()),
            style = MaterialTheme.typography.displayLarge,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.testTag("timer.remaining"),
        )
        Text(
            state.timerRemainingMs().toString(),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag("timer.remaining_ms"),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(
                onClick = { state.resumeTimer() },
                modifier = Modifier.testTag("timer.resume"),
            ) { Text("Resume") }
            OutlinedButton(
                onClick = { state.resetTimer() },
                modifier = Modifier.testTag("timer.reset"),
            ) { Text("Reset") }
            OutlinedButton(
                onClick = { state.fastForwardDone() },
                modifier = Modifier.testTag("timer.fast_forward_done"),
            ) { Text("Skip") }
        }
    }
}
