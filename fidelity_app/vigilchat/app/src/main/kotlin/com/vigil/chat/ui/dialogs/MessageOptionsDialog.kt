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
 * Message options dialog. Owns the screen_marker while active.
 */
@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
fun MessageOptionsDialog(state: AppState) {
    AlertDialog(
        onDismissRequest = { state.closeMessageOptions() },
        modifier = Modifier
            .testTag("message_options")
            .semantics { testTagsAsResourceId = true },
        title = {
            Text(
                "Message options",
                modifier = Modifier.testTag("message_options.title"),
            )
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                // Single-authority marker for this dialog state.
                ScreenMarker("message_options")
                Text(
                    "Choose an action for the selected message.",
                    modifier = Modifier.testTag("message_options.subtitle"),
                )
            }
        },
        confirmButton = {
            Button(
                onClick = { state.openDeleteConfirm() },
                modifier = Modifier.testTag("message_options.delete"),
            ) { Text("Delete") }
        },
        dismissButton = {
            OutlinedButton(
                onClick = { state.closeMessageOptions() },
                modifier = Modifier.testTag("message_options.cancel"),
            ) { Text("Cancel") }
        },
    )
}
