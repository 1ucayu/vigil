package com.vigil.chat.ui.dialogs

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.compose.ui.unit.dp
import com.vigil.chat.AppState
import com.vigil.chat.ui.components.ScreenMarker

/**
 * Delete-confirmation dialog. Owns the screen_marker while active.
 */
@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
fun DeleteConfirmDialog(state: AppState) {
    val targetId = state.selectedMessageId ?: ""
    val targetBody = state.currentMessages()
        .firstOrNull { it.id == targetId }
        ?.body ?: ""

    AlertDialog(
        onDismissRequest = { state.cancelDelete() },
        modifier = Modifier
            .testTag("delete_confirm")
            .semantics { testTagsAsResourceId = true },
        title = {
            Text(
                "Delete message?",
                modifier = Modifier.testTag("delete_confirm.title"),
            )
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                // Single-authority marker for this dialog state.
                ScreenMarker("delete_confirm")
                Text(
                    text = targetBody,
                    modifier = Modifier.testTag("delete_confirm.message_text"),
                )
            }
        },
        confirmButton = {
            Button(
                onClick = { state.confirmDelete() },
                modifier = Modifier.testTag("delete_confirm.confirm"),
            ) { Text("Delete") }
        },
        dismissButton = {
            OutlinedButton(
                onClick = { state.cancelDelete() },
                modifier = Modifier.testTag("delete_confirm.cancel"),
            ) { Text("Cancel") }
        },
    )
}
