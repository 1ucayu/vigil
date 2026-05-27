package com.vigil.chat.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.chat.AppState
import com.vigil.chat.Dialog
import com.vigil.chat.Message
import com.vigil.chat.ui.components.ScreenMarker
import com.vigil.chat.ui.dialogs.DeleteConfirmDialog
import com.vigil.chat.ui.dialogs.MessageOptionsDialog

@Composable
fun ThreadScreen(state: AppState) {
    val tid = state.currentThreadId ?: return
    val dialog = state.activeDialog

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        // Single-authority marker: only emit if no dialog is overlaying.
        if (dialog == null) {
            ScreenMarker("thread")
        }

        // Bound template parameter and title.
        Text(
            text = tid,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag("thread.thread_id.$tid"),
        )
        Text(
            text = state.currentThreadTitle(),
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("thread.title"),
        )

        Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .testTag("thread.message_list"),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                items(state.currentMessages(), key = { it.id }) { msg ->
                    MessageRow(msg, state)
                }
            }
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                value = state.messageDraft,
                onValueChange = { state.setDraft(it) },
                modifier = Modifier
                    .weight(1f)
                    .testTag("thread.message_input"),
                placeholder = { Text("Message") },
                singleLine = true,
            )
            Button(
                onClick = { state.openAttachmentPicker() },
                modifier = Modifier.testTag("thread.attach"),
            ) { Text("Attach") }
            Button(
                onClick = { state.sendMessage() },
                modifier = Modifier.testTag("thread.send"),
            ) { Text("Send") }
        }
    }

    // Dialogs render on top and own the screen_marker while active.
    when (dialog) {
        Dialog.MESSAGE_OPTIONS -> MessageOptionsDialog(state)
        Dialog.DELETE_CONFIRM -> DeleteConfirmDialog(state)
        null -> Unit
    }
}

@Composable
private fun MessageRow(msg: Message, state: AppState) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { state.openMessageOptions(msg.id) }
            .testTag("thread.message.${msg.id}.options"),
    ) {
        Column(modifier = Modifier.padding(10.dp)) {
            Text(
                text = if (msg.outgoing) "Me" else state.currentThreadTitle(),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = msg.body,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.testTag("thread.message.${msg.id}.text"),
            )
        }
    }
}
