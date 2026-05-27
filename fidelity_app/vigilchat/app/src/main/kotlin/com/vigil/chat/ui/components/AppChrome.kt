package com.vigil.chat.ui.components

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.Contacts
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import com.vigil.chat.AppState
import com.vigil.chat.Screen

@OptIn(ExperimentalMaterial3Api::class, androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
fun AppChrome(
    state: AppState,
    content: @Composable (PaddingValues) -> Unit,
) {
    Scaffold(
        modifier = Modifier.semantics { testTagsAsResourceId = true },
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Text(
                        text = titleFor(state),
                        modifier = Modifier.testTag("app.top_bar.title"),
                    )
                },
                colors = TopAppBarDefaults.centerAlignedTopAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                    titleContentColor = MaterialTheme.colorScheme.onSurface,
                ),
            )
        },
        bottomBar = { BottomNav(state) },
        containerColor = MaterialTheme.colorScheme.background,
        content = { innerPadding ->
            Surface(color = MaterialTheme.colorScheme.background) {
                content(innerPadding)
            }
        },
    )
}

@Composable
private fun BottomNav(state: AppState) {
    NavigationBar(
        modifier = Modifier.testTag("app.bottom_nav"),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        NavigationBarItem(
            selected = state.screen == Screen.INBOX,
            onClick = { state.navigate(Screen.INBOX) },
            icon = { Icon(Icons.AutoMirrored.Filled.Chat, contentDescription = "Inbox") },
            label = { Text("Inbox") },
            modifier = Modifier.testTag("nav.open_inbox"),
        )
        NavigationBarItem(
            selected = state.screen == Screen.CONTACTS,
            onClick = { state.navigate(Screen.CONTACTS) },
            icon = { Icon(Icons.Filled.Contacts, contentDescription = "Contacts") },
            label = { Text("Contacts") },
            modifier = Modifier.testTag("nav.open_contacts"),
        )
        NavigationBarItem(
            selected = state.screen == Screen.SETTINGS,
            onClick = { state.navigate(Screen.SETTINGS) },
            icon = { Icon(Icons.Filled.Settings, contentDescription = "Settings") },
            label = { Text("Settings") },
            modifier = Modifier.testTag("nav.open_settings"),
        )
    }
}

private fun titleFor(state: AppState): String = when (state.screen) {
    Screen.INBOX -> "VigilChat"
    Screen.THREAD -> state.currentThreadTitle().ifBlank { "Thread" }
    Screen.ATTACHMENT_PICKER -> "Attach"
    Screen.CONTACTS -> "Contacts"
    Screen.SETTINGS -> "Settings"
}
