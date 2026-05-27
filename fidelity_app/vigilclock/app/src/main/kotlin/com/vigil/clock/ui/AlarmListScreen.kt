package com.vigil.clock.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
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
fun AlarmListScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("alarm_list")
        Text(
            "Alarms",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("alarm_list.title"),
        )

        state.alarms.forEach { alarm ->
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("alarm.row.${alarm.id}"),
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            "%02d:%02d".format(alarm.hour, alarm.minute),
                            style = MaterialTheme.typography.titleLarge,
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.testTag("alarm.time.${alarm.id}"),
                        )
                        Text(
                            alarm.label,
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.testTag("alarm.label.${alarm.id}"),
                        )
                    }
                    OutlinedButton(
                        onClick = { state.startEditAlarm(alarm.id) },
                        modifier = Modifier.testTag("alarm.edit.${alarm.id}"),
                    ) { Text("Edit") }
                    Switch(
                        checked = alarm.enabled,
                        onCheckedChange = { state.toggleAlarm(alarm.id) },
                        modifier = Modifier.testTag("alarm.toggle.${alarm.id}"),
                    )
                }
            }
        }
    }
}
