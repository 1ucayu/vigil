package com.vigil.market

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.vigil.market.ui.AddressSelectScreen
import com.vigil.market.ui.CartScreen
import com.vigil.market.ui.CatalogScreen
import com.vigil.market.ui.HomeScreen
import com.vigil.market.ui.OrdersScreen
import com.vigil.market.ui.PaymentConfirmScreen
import com.vigil.market.ui.PaymentSuccessScreen
import com.vigil.market.ui.ProductDetailScreen
import com.vigil.market.ui.SearchScreen
import com.vigil.market.ui.SettingsScreen
import com.vigil.market.ui.components.AppChrome
import com.vigil.market.ui.dialogs.PaymentConfirmDialog
import com.vigil.market.ui.dialogs.RemoveItemDialog
import com.vigil.market.ui.theme.VigilMarketTheme

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
