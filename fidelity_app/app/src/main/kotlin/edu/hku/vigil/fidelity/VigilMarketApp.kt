package edu.hku.vigil.fidelity

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import edu.hku.vigil.fidelity.ui.AddressSelectScreen
import edu.hku.vigil.fidelity.ui.CartScreen
import edu.hku.vigil.fidelity.ui.CatalogScreen
import edu.hku.vigil.fidelity.ui.HomeScreen
import edu.hku.vigil.fidelity.ui.OrdersScreen
import edu.hku.vigil.fidelity.ui.PaymentConfirmScreen
import edu.hku.vigil.fidelity.ui.PaymentSuccessScreen
import edu.hku.vigil.fidelity.ui.ProductDetailScreen
import edu.hku.vigil.fidelity.ui.SearchScreen
import edu.hku.vigil.fidelity.ui.SettingsScreen
import edu.hku.vigil.fidelity.ui.components.AppChrome
import edu.hku.vigil.fidelity.ui.dialogs.PaymentConfirmDialog
import edu.hku.vigil.fidelity.ui.dialogs.RemoveItemDialog
import edu.hku.vigil.fidelity.ui.theme.VigilMarketTheme

@Composable
fun VigilMarketApp() {
    val state = remember { AppState() }
    val ctx = LocalContext.current

    VigilMarketTheme {
        AppChrome(state = state) { innerPadding ->
            // Deterministic system-back routing.
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
                    Screen.HOME             -> HomeScreen(state)
                    Screen.SEARCH           -> SearchScreen(state)
                    Screen.CATALOG          -> CatalogScreen(state)
                    Screen.PRODUCT_DETAIL   -> ProductDetailScreen(state)
                    Screen.CART_EMPTY       -> CartScreen(state, empty = true)
                    Screen.CART             -> CartScreen(state, empty = false)
                    Screen.ADDRESS_SELECT   -> AddressSelectScreen(state)
                    Screen.PAYMENT_CONFIRM  -> PaymentConfirmScreen(state)
                    Screen.PAYMENT_SUCCESS  -> PaymentSuccessScreen(state)
                    Screen.ORDERS           -> OrdersScreen(state)
                    Screen.SETTINGS         -> SettingsScreen(state)
                }

                if (state.removeDialogOpen) RemoveItemDialog(state)
                if (state.paymentDialogOpen) PaymentConfirmDialog(state)
            }
        }
    }
}
