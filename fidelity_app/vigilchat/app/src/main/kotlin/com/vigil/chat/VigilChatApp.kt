package com.vigil.chat

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.vigil.chat.ui.AttachmentPickerScreen
import com.vigil.chat.ui.ContactsScreen
import com.vigil.chat.ui.InboxScreen
import com.vigil.chat.ui.SettingsScreen
import com.vigil.chat.ui.ThreadScreen
import com.vigil.chat.ui.components.AppChrome
import com.vigil.chat.ui.theme.VigilChatTheme

@Composable
fun VigilChatApp() {
    val state = remember { AppState() }
    val ctx = LocalContext.current

    VigilChatTheme {
        AppChrome(state = state) { innerPadding ->
            BackHandler(enabled = true) {
                val consumed = state.handleBack()
                if (!consumed) {
                    (ctx as? android.app.Activity)?.finish()
                }
            }

            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding),
            ) {
                when (state.screen) {
                    Screen.INBOX -> InboxScreen(state)
                    Screen.THREAD -> ThreadScreen(state)
                    Screen.ATTACHMENT_PICKER -> AttachmentPickerScreen(state)
                    Screen.CONTACTS -> ContactsScreen(state)
                    Screen.SETTINGS -> SettingsScreen(state)
                }
            }
        }
    }
}
