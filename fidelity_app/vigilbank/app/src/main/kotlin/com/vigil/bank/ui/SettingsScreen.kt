package com.vigil.bank.ui

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
import com.vigil.bank.AppState
import com.vigil.bank.Screen
import com.vigil.bank.ui.components.ScreenMarker

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
                        "Biometric unlock",
                        modifier = Modifier
                            .weight(1f)
                            .testTag("settings.biometric_unlock.label"),
                    )
                    Switch(
                        checked = state.biometricUnlock,
                        onCheckedChange = { state.biometricUnlock = it },
                        modifier = Modifier.testTag("settings.biometric_unlock"),
                    )
                }

                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        "Transfer notifications",
                        modifier = Modifier
                            .weight(1f)
                            .testTag("settings.transfer_notifications.label"),
                    )
                    Switch(
                        checked = state.transferNotifications,
                        onCheckedChange = { state.transferNotifications = it },
                        modifier = Modifier.testTag("settings.transfer_notifications"),
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
