package edu.hku.vigil.fidelity.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
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
import edu.hku.vigil.fidelity.AppState
import edu.hku.vigil.fidelity.Screen
import edu.hku.vigil.fidelity.ui.components.ScreenMarker

@Composable
fun SettingsScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("settings")
        Text(
            "Settings",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("settings.title"),
        )

        Card(modifier = Modifier.testTag("settings.preferences_card")) {
            Column(
                modifier = Modifier.padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        "High contrast",
                        modifier = Modifier
                            .weight(1f)
                            .testTag("settings.high_contrast.label"),
                    )
                    Switch(
                        checked = state.highContrast,
                        onCheckedChange = { state.highContrast = it },
                        modifier = Modifier.testTag("settings.high_contrast"),
                    )
                }

                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        "Confirm payments",
                        modifier = Modifier
                            .weight(1f)
                            .testTag("settings.confirm_payments.label"),
                    )
                    Switch(
                        checked = state.confirmPayments,
                        onCheckedChange = { state.confirmPayments = it },
                        modifier = Modifier.testTag("settings.confirm_payments"),
                    )
                }
            }
        }

        Button(
            onClick = { state.resetDemo() },
            modifier = Modifier.testTag("settings.reset_demo"),
        ) { Text("Reset demo") }

        OutlinedButton(
            onClick = { state.navigate(Screen.HOME) },
            modifier = Modifier.testTag("settings.back_home"),
        ) { Text("Home") }
    }
}
