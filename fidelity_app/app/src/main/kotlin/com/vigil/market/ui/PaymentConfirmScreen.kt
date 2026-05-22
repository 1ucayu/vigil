package com.vigil.market.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
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
import com.vigil.market.AppState
import com.vigil.market.Screen
import com.vigil.market.ui.components.ScreenMarker
import com.vigil.market.ui.components.cents

@Composable
fun PaymentConfirmScreen(state: AppState) {
    val product = state.selectedProduct ?: return
    val address = state.selectedAddress ?: return

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("payment_confirm")
        Text(
            "Confirm payment",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("payment_confirm.title"),
        )

        Card(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("payment.summary_card"),
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    product.name,
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.testTag("payment.product_name"),
                )
                Text(
                    "Quantity: ${state.quantity}",
                    modifier = Modifier.testTag("payment.quantity"),
                )
                Text(
                    "Address: ${address.label}",
                    modifier = Modifier.testTag("payment.address_label"),
                )
                Text(
                    "Total: ${cents(state.totalCents())}",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.testTag("payment.total_amount"),
                )
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(
                onClick = { state.navigate(Screen.ADDRESS_SELECT) },
                modifier = Modifier.testTag("payment_confirm.cancel"),
            ) { Text("Cancel") }
            Button(
                onClick = { state.openPaymentDialog() },
                modifier = Modifier
                    .weight(1f)
                    .testTag("payment_confirm.pay"),
            ) { Text("Pay") }
        }
    }
}
