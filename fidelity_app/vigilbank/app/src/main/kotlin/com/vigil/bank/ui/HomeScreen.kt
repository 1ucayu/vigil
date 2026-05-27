package com.vigil.bank.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.bank.AppState
import com.vigil.bank.Screen
import com.vigil.bank.ui.components.ScreenMarker
import com.vigil.bank.ui.components.cents

@Composable
fun HomeScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        ScreenMarker("home")

        Text(
            "Welcome back",
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("home.title"),
        )
        Text(
            "Deterministic banking, seeded data, stable UI markers.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag("home.subtitle"),
        )

        ElevatedCard(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("home.balance_card"),
        ) {
            Column(
                modifier = Modifier.padding(18.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    "Available balance",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                )
                Text(
                    cents(state.balanceCents),
                    style = MaterialTheme.typography.headlineLarge,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.testTag("home.balance"),
                )
                Text(
                    "Daily limit: ${cents(state.dailyLimitCents)}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag("home.daily_limit"),
                )
            }
        }

        Card(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("home.quick_actions"),
        ) {
            Column(
                modifier = Modifier.padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    "Quick actions",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        onClick = { state.openRecipients() },
                        modifier = Modifier.testTag("home.open_recipients"),
                    ) { Text("New transfer") }

                    OutlinedButton(
                        onClick = { state.navigate(Screen.HISTORY) },
                        modifier = Modifier.testTag("home.open_history"),
                    ) { Text("History") }

                    OutlinedButton(
                        onClick = { state.navigate(Screen.SETTINGS) },
                        modifier = Modifier.testTag("home.open_settings"),
                    ) { Text("Settings") }
                }
            }
        }
    }
}
