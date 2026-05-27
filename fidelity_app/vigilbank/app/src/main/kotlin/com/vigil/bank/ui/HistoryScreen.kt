package com.vigil.bank.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
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
fun HistoryScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("history")
        Text(
            "Transfer history",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("history.title"),
        )

        for (entry in state.history) {
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("history.row.${entry.id}"),
            ) {
                Row(
                    modifier = Modifier.padding(14.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Text(
                        "${entry.id}  -> ${entry.recipientName}  ${cents(entry.amountCents)}  \"${entry.memo}\"",
                        style = MaterialTheme.typography.bodyLarge,
                        modifier = Modifier.testTag("history.row.${entry.id}.summary"),
                    )
                }
            }
        }

        Button(
            onClick = { state.backHomeFromHistory() },
            modifier = Modifier
                .fillMaxWidth()
                .testTag("history.back_home"),
        ) { Text("Home") }
    }
}
