package com.vigil.chat.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.chat.AppState
import com.vigil.chat.data.Threads
import com.vigil.chat.ui.components.ScreenMarker

@Composable
fun InboxScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("inbox")

        Text(
            "Inbox",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("inbox.title"),
        )

        LazyColumn(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("inbox.thread_list"),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(Threads.order, key = { it }) { tid ->
                ThreadRow(tid, state)
            }
        }
    }
}

@Composable
private fun ThreadRow(threadId: String, state: AppState) {
    val title = Threads.titleOf(threadId) ?: threadId
    val preview = Threads.previewOf(threadId)
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { state.openThread(threadId) }
            .testTag("inbox.thread_row.$threadId.open"),
    ) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(
                title,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.testTag("inbox.thread_row.$threadId.title"),
            )
            Text(
                preview,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag("inbox.thread_row.$threadId.preview"),
            )
        }
    }
}
