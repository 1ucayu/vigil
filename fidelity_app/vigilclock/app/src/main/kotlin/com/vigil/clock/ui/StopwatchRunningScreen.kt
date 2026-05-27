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
fun StopwatchRunningScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        ScreenMarker("stopwatch_running")
        Text(
            "Running",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("stopwatch_running.title"),
        )
        Text(
            formatMs(state.stopwatchElapsedMs),
            style = MaterialTheme.typography.displayLarge,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.testTag("stopwatch.elapsed"),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(
                onClick = { state.pauseSw() },
                modifier = Modifier.testTag("stopwatch.pause"),
            ) { Text("Pause") }
            OutlinedButton(
                onClick = { state.lap() },
                modifier = Modifier.testTag("stopwatch.lap"),
            ) { Text("Lap") }
            OutlinedButton(
                onClick = { state.resetSw() },
                modifier = Modifier.testTag("stopwatch.reset"),
            ) { Text("Reset") }
        }
        if (state.stopwatchLaps.isNotEmpty()) {
            Text("Laps", style = MaterialTheme.typography.titleMedium)
            state.stopwatchLaps.forEachIndexed { i, lapMs ->
                Text(
                    "Lap ${i + 1}: ${formatMs(lapMs)}",
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.testTag("stopwatch.lap.row.${i + 1}"),
                )
            }
        }
    }
}
