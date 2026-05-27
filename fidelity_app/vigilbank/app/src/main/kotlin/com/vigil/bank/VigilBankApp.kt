package com.vigil.bank

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.vigil.bank.ui.HistoryScreen
import com.vigil.bank.ui.HomeScreen
import com.vigil.bank.ui.OtpConfirmScreen
import com.vigil.bank.ui.RecipientsScreen
import com.vigil.bank.ui.SettingsScreen
import com.vigil.bank.ui.TransferConfirmScreen
import com.vigil.bank.ui.TransferFormScreen
import com.vigil.bank.ui.TransferSuccessScreen
import com.vigil.bank.ui.components.AppChrome
import com.vigil.bank.ui.theme.VigilBankTheme

@Composable
fun VigilBankApp() {
    val state = remember { AppState() }
    val ctx = LocalContext.current

    VigilBankTheme {
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
                    Screen.HOME              -> HomeScreen(state)
                    Screen.RECIPIENTS        -> RecipientsScreen(state)
                    Screen.TRANSFER_FORM     -> TransferFormScreen(state)
                    Screen.TRANSFER_CONFIRM  -> TransferConfirmScreen(state)
                    Screen.OTP_CONFIRM       -> OtpConfirmScreen(state)
                    Screen.TRANSFER_SUCCESS  -> TransferSuccessScreen(state)
                    Screen.HISTORY           -> HistoryScreen(state)
                    Screen.SETTINGS          -> SettingsScreen(state)
                }
            }
        }
    }
}
