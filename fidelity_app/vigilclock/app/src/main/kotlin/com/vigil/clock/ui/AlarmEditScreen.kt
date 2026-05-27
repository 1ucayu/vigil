package com.vigil.clock.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.clock.AppState
import com.vigil.clock.ui.components.ScreenMarker

@Composable
fun AlarmEditScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("alarm_edit")
        Text(
            "Edit alarm",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("alarm_edit.title"),
        )

        Card {
            Row(
                modifier = Modifier.padding(18.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Hour", style = MaterialTheme.typography.labelMedium)
                    OutlinedButton(
                        onClick = { state.hourInc() },
                        modifier = Modifier.testTag("alarm_edit.hour.increment"),
                    ) { Text("+") }
                    Text(
                        "%02d".format(state.editingHour),
                        style = MaterialTheme.typography.displaySmall,
                        modifier = Modifier.testTag("alarm_edit.hour"),
                    )
                    OutlinedButton(
                        onClick = { state.hourDec() },
                        modifier = Modifier.testTag("alarm_edit.hour.decrement"),
                    ) { Text("-") }
                }
                Text(
                    ":",
                    style = MaterialTheme.typography.displayMedium,
                )
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Minute", style = MaterialTheme.typography.labelMedium)
                    OutlinedButton(
                        onClick = { state.minInc() },
                        modifier = Modifier.testTag("alarm_edit.minute.increment"),
                    ) { Text("+") }
                    Text(
                        "%02d".format(state.editingMinute),
                        style = MaterialTheme.typography.displaySmall,
                        modifier = Modifier.testTag("alarm_edit.minute"),
                    )
                    OutlinedButton(
                        onClick = { state.minDec() },
                        modifier = Modifier.testTag("alarm_edit.minute.decrement"),
                    ) { Text("-") }
                }
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(
                onClick = { state.saveAlarm() },
                modifier = Modifier.testTag("alarm_edit.save"),
            ) { Text("Save") }
            OutlinedButton(
                onClick = { state.cancelAlarm() },
                modifier = Modifier.testTag("alarm_edit.cancel"),
            ) { Text("Cancel") }
        }
    }
}
