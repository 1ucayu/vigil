package com.vigil.clock.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.clock.AppState
import com.vigil.clock.ui.components.ScreenMarker

@Composable
fun StopwatchIdleScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        ScreenMarker("stopwatch_idle")
        Text(
            "Stopwatch",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("stopwatch_idle.title"),
        )
        // stopwatch_elapsed_ms == 0 invariant is encoded in gold/guards.json.
        Text(
            "00:00.0",
            style = MaterialTheme.typography.displayLarge,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.testTag("stopwatch_idle.zero"),
        )
        Text(
            state.stopwatchElapsedMs.toString(),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag("stopwatch.elapsed_ms"),
        )
        Button(
            onClick = { state.startSw() },
            modifier = Modifier.testTag("stopwatch.start"),
        ) { Text("Start") }
    }
}
