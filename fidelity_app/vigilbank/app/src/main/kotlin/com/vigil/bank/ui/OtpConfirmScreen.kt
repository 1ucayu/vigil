package com.vigil.bank.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
fun OtpConfirmScreen(state: AppState) {
    val recipient = state.selectedRecipient ?: return
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("otp_confirm")
        Text(
            "Enter OTP",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("otp_confirm.title"),
        )

        Card(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("otp_confirm.summary_card"),
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
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
                Text(
                    "Amount: ${cents(state.amountCents)}",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.testTag("otp_confirm.amount"),
                )
            }
        }

        OutlinedTextField(
            value = state.otpDigits,
            onValueChange = { state.updateOtpDigits(it) },
            label = { Text("OTP code") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier
                .fillMaxWidth()
                .testTag("otp_confirm.otp_input"),
        )

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(
                onClick = { state.cancelOtp() },
                modifier = Modifier.testTag("otp_confirm.cancel"),
            ) { Text("Cancel") }
            Button(
                onClick = { state.confirmOtp() },
                modifier = Modifier
                    .weight(1f)
                    .testTag("otp_confirm.confirm"),
            ) { Text("Confirm") }
        }
    }
}
