package com.vigil.bank.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.bank.AppState
import com.vigil.bank.ui.components.ScreenMarker
import com.vigil.bank.ui.components.cents

@Composable
fun TransferFormScreen(state: AppState) {
    val recipient = state.selectedRecipient ?: return
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("transfer_form")
        Text(
            "Transfer details",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("transfer_form.title"),
        )

        Card(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("transfer_form.recipient_card"),
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    "To",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                )
                Text(
                    recipient.displayName,
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.testTag("transfer.recipient_label"),
                )
                Text(
                    recipient.accountMasked,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag("transfer.recipient_account"),
                )
            }
        }

        // Bind amount as a numeric text field; the underlying state is integer cents.
        val amountText = if (state.amountCents == 0) "" else state.amountCents.toString()
        OutlinedTextField(
            value = amountText,
            onValueChange = { input ->
                val digits = input.filter { it.isDigit() }.take(9)
                state.updateAmountCents(if (digits.isEmpty()) 0 else digits.toInt())
            },
            label = { Text("Amount (cents)") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier
                .fillMaxWidth()
                .testTag("transfer.amount.input"),
        )
        Text(
            "Preview: ${cents(state.amountCents)}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag("transfer.amount.preview"),
        )

        OutlinedTextField(
            value = state.memo,
            onValueChange = { state.updateMemo(it) },
            label = { Text("Memo") },
            modifier = Modifier
                .fillMaxWidth()
                .testTag("transfer.memo.input"),
        )

        Button(
            onClick = { state.continueToConfirm() },
            modifier = Modifier
                .fillMaxWidth()
                .testTag("transfer.continue"),
        ) { Text("Continue") }
    }
}
