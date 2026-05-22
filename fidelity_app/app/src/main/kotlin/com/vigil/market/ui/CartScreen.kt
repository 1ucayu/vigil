package com.vigil.market.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.market.AppState
import com.vigil.market.Screen
import com.vigil.market.ui.components.ScreenMarker
import com.vigil.market.ui.components.cents

@Composable
fun CartScreen(state: AppState, empty: Boolean) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (empty || state.selectedProduct == null) {
            ScreenMarker("cart_empty")
            Text(
                "Your cart is empty.",
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.testTag("cart_empty.message"),
            )
            Text(
                "Browse the deterministic catalog to seed a replayable purchase flow.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Button(
                onClick = { state.navigate(Screen.CATALOG) },
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("cart_empty.go_catalog"),
            ) { Text("Browse catalog") }
            OutlinedButton(
                onClick = { state.navigate(Screen.HOME) },
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("cart_empty.back_home"),
            ) { Text("Home") }
            return
        }

        val product = state.selectedProduct!!
        ScreenMarker("cart")
        Text(
            "Cart",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("cart.title"),
        )

        Card(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("cart.summary_card"),
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    product.name,
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.testTag("cart.item_name"),
                )
                Text(
                    "Unit: ${cents(product.priceCents)}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag("cart.unit_price"),
                )

                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    IconButton(
                        onClick = { state.decQty() },
                        modifier = Modifier
                            .testTag("cart.qty_minus")
                            .semantics { contentDescription = "decrease quantity" },
                    ) { Text("-") }
                    Text(
                        "${state.quantity}",
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.testTag("cart.qty_value"),
                    )
                    IconButton(
                        onClick = { state.incQty() },
                        modifier = Modifier
                            .testTag("cart.qty_plus")
                            .semantics { contentDescription = "increase quantity" },
                    ) { Text("+") }
                }

                Text(
                    "Total: ${cents(state.totalCents())}",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.testTag("cart.total"),
                )
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(
                onClick = { state.openRemoveDialog() },
                modifier = Modifier.testTag("cart.remove_item"),
            ) { Text("Remove") }
            Button(
                onClick = { state.startCheckout() },
                modifier = Modifier
                    .weight(1f)
                    .testTag("cart.checkout"),
            ) { Text("Checkout") }
        }
    }
}
