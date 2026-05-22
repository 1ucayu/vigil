package edu.hku.vigil.fidelity.ui.dialogs

import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import edu.hku.vigil.fidelity.AppState

@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
fun PaymentConfirmDialog(state: AppState) {
    AlertDialog(
        onDismissRequest = { state.closePaymentDialog() },
        modifier = Modifier
            .testTag("payment_dialog")
            .semantics { testTagsAsResourceId = true },
        title = {
            Text("Confirm payment", modifier = Modifier.testTag("payment_dialog.title"))
        },
        text = {
            Text(
                "Pay $${"%.2f".format(state.totalCents() / 100.0)} for " +
                    "${state.selectedProduct?.name ?: ""} (x${state.quantity})?",
                modifier = Modifier.testTag("payment_dialog.message"),
            )
        },
        confirmButton = {
            Button(
                onClick = { state.confirmPayment() },
                modifier = Modifier.testTag("payment_dialog.confirm"),
            ) { Text("Pay") }
        },
        dismissButton = {
            Button(
                onClick = { state.closePaymentDialog() },
                modifier = Modifier.testTag("payment_dialog.cancel"),
            ) { Text("Cancel") }
        },
    )
}
