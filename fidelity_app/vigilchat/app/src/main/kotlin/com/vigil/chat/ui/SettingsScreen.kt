package com.vigil.chat.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.chat.AppState
import com.vigil.chat.Screen
import com.vigil.chat.ui.components.ScreenMarker

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

        Card(modifier = Modifier.testTag("settings.info_card")) {
            Text(
                "VigilChat fidelity app. Seeded threads, contacts, and attachments. No network, no disk I/O.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier
                    .padding(14.dp)
                    .testTag("settings.info_text"),
            )
        }

        Button(
            onClick = { state.resetDemo() },
            modifier = Modifier.testTag("settings.reset_demo"),
        ) { Text("Reset demo") }

        OutlinedButton(
            onClick = { state.navigate(Screen.INBOX) },
            modifier = Modifier.testTag("settings.back_inbox"),
        ) { Text("Inbox") }
    }
}
