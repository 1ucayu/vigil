package com.vigil.chat.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.chat.AppState
import com.vigil.chat.data.Attachments
import com.vigil.chat.ui.components.ScreenMarker

@Composable
fun AttachmentPickerScreen(state: AppState) {
    val tid = state.currentThreadId ?: ""

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        ScreenMarker("attachment_picker")

        Text(
            "Choose attachment",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("attachment_picker.title"),
        )
        Text(
            text = tid,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag("thread.thread_id.$tid"),
        )

        for (att in Attachments.all) {
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { state.pickAttachment(att) }
                    .testTag("attachment_picker.item.${att.id}"),
            ) {
                Text(
                    text = att.name,
                    modifier = Modifier
                        .padding(14.dp)
                        .testTag("attachment_picker.item.${att.id}.label"),
                )
            }
        }

        OutlinedButton(
            onClick = { state.cancelAttachment() },
            modifier = Modifier.testTag("attachment_picker.cancel"),
        ) { Text("Cancel") }
    }
}
