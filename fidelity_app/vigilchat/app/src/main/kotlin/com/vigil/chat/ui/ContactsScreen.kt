package com.vigil.chat.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.chat.AppState
import com.vigil.chat.data.Contacts
import com.vigil.chat.ui.components.ScreenMarker

@Composable
fun ContactsScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        ScreenMarker("contacts")

        Text(
            "Contacts",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("contacts.title"),
        )

        for (c in Contacts.all) {
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { state.openContact(c.id) }
                    .testTag("contacts.contact_row.${c.id}.open"),
            ) {
                Text(
                    text = c.name,
                    modifier = Modifier
                        .padding(14.dp)
                        .testTag("contacts.contact_row.${c.id}.name"),
                )
            }
        }
    }
}
