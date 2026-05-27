package com.vigil.bank.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.bank.AppState
import com.vigil.bank.ui.components.ScreenMarker
import com.vigil.bank.ui.components.cents

@Composable
fun TransferSuccessScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("transfer_success")
        ElevatedCard(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("transfer_success.card"),
        ) {
            Column(
                modifier = Modifier.padding(18.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    "Transfer successful",
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.testTag("transfer_success.title"),
                )
                Text(
                    "Your transfer has been recorded.",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag("transfer_success.message"),
                )
                Text(
                    "Updated balance: ${cents(state.balanceCents)}",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.testTag("transfer_success.balance"),
                )
            }
        }
        Button(
            onClick = { state.backHomeFromSuccess() },
            modifier = Modifier
                .fillMaxWidth()
                .testTag("transfer_success.back_home"),
        ) { Text("Home") }
    }
}
