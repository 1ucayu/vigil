package com.vigil.market.ui.components

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ReceiptLong
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material.icons.filled.Storefront
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
import com.vigil.market.AppState
import com.vigil.market.Screen

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
            selected = state.screen == Screen.SEARCH,
            onClick = { state.navigate(Screen.SEARCH) },
            icon = { Icon(Icons.Filled.Search, contentDescription = "Search") },
            label = { Text("Search") },
            modifier = Modifier.testTag("nav.open_search"),
        )
        NavigationBarItem(
            selected = state.screen == Screen.CATALOG || state.screen == Screen.PRODUCT_DETAIL,
            onClick = { state.navigate(Screen.CATALOG) },
            icon = { Icon(Icons.Filled.Storefront, contentDescription = "Catalog") },
            label = { Text("Catalog") },
            modifier = Modifier.testTag("nav.open_catalog"),
        )
        NavigationBarItem(
            selected = state.screen == Screen.CART || state.screen == Screen.CART_EMPTY,
            onClick = { state.openCart() },
            icon = { Icon(Icons.Filled.ShoppingCart, contentDescription = "Cart") },
            label = { Text("Cart") },
            modifier = Modifier.testTag("nav.open_cart"),
        )
        NavigationBarItem(
            selected = state.screen == Screen.ORDERS,
            onClick = { state.navigate(Screen.ORDERS) },
            icon = { Icon(Icons.AutoMirrored.Filled.ReceiptLong, contentDescription = "Orders") },
            label = { Text("Orders") },
            modifier = Modifier.testTag("nav.open_orders"),
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
    Screen.HOME -> "VigilMarket"
    Screen.SEARCH -> "Search"
    Screen.CATALOG -> "Catalog"
    Screen.PRODUCT_DETAIL -> "Product"
    Screen.CART_EMPTY,
    Screen.CART -> "Cart"
    Screen.ADDRESS_SELECT -> "Delivery"
    Screen.PAYMENT_CONFIRM -> "Payment"
    Screen.PAYMENT_SUCCESS -> "Receipt"
    Screen.ORDERS -> "Orders"
    Screen.SETTINGS -> "Settings"
}
