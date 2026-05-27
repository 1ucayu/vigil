package com.vigil.market.ui.dialogs

import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import com.vigil.market.AppState

@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
fun RemoveItemDialog(state: AppState) {
    AlertDialog(
        onDismissRequest = { state.closeRemoveDialog() },
        modifier = Modifier
            .testTag("remove_dialog")
            .semantics { testTagsAsResourceId = true },
        title = {
            Text("Remove item?", modifier = Modifier.testTag("remove_dialog.title"))
        },
        text = {
            Text(
                "Remove ${state.selectedProduct?.name ?: ""} from your cart?",
                modifier = Modifier.testTag("remove_dialog.message"),
            )
        },
        confirmButton = {
            Button(
                onClick = { state.confirmRemove() },
                modifier = Modifier.testTag("remove_dialog.confirm"),
            ) { Text("Remove") }
        },
        dismissButton = {
            Button(
                onClick = { state.closeRemoveDialog() },
                modifier = Modifier.testTag("remove_dialog.cancel"),
            ) { Text("Cancel") }
        },
    )
}
