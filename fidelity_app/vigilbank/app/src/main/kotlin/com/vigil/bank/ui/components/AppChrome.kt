package com.vigil.bank.ui.components

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ReceiptLong
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.People
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
import com.vigil.bank.AppState
import com.vigil.bank.Screen

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
                        text = titleFor(state.screen),
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
            selected = state.screen == Screen.HOME,
            onClick = { state.navigate(Screen.HOME) },
            icon = { Icon(Icons.Filled.Home, contentDescription = "Home") },
            label = { Text("Home") },
            modifier = Modifier.testTag("nav.open_home"),
        )
        NavigationBarItem(
            selected = state.screen == Screen.RECIPIENTS,
            onClick = { state.navigate(Screen.RECIPIENTS) },
            icon = { Icon(Icons.Filled.People, contentDescription = "Recipients") },
            label = { Text("Recipients") },
            modifier = Modifier.testTag("nav.open_recipients"),
        )
        NavigationBarItem(
            selected = state.screen == Screen.HISTORY,
            onClick = { state.navigate(Screen.HISTORY) },
            icon = { Icon(Icons.AutoMirrored.Filled.ReceiptLong, contentDescription = "History") },
            label = { Text("History") },
            modifier = Modifier.testTag("nav.open_history"),
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

private fun titleFor(screen: Screen): String = when (screen) {
    Screen.HOME -> "VigilBank"
    Screen.RECIPIENTS -> "Recipients"
    Screen.TRANSFER_FORM -> "Transfer"
    Screen.TRANSFER_CONFIRM -> "Confirm Transfer"
    Screen.OTP_CONFIRM -> "OTP"
    Screen.TRANSFER_SUCCESS -> "Receipt"
    Screen.HISTORY -> "History"
    Screen.SETTINGS -> "Settings"
}
