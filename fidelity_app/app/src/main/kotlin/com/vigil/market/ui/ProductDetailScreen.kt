package com.vigil.market.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.market.AppState
import com.vigil.market.ui.components.ScreenMarker
import com.vigil.market.ui.components.cents

@Composable
fun ProductDetailScreen(state: AppState) {
    val product = state.selectedProduct ?: return
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        ScreenMarker("product_detail")

        ElevatedCard(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("product_detail.card"),
        ) {
            Column(
                modifier = Modifier.padding(18.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    "Selected item",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.testTag("product_detail.title"),
                )
                Text(
                    product.name,
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.testTag("product_detail.name"),
                )
                Text(
                    cents(product.priceCents),
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.secondary,
                    modifier = Modifier.testTag("product_detail.price"),
                )
                Text(
                    product.description,
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag("product_detail.description"),
                )
            }
        }

        Button(
            onClick = { state.addToCart() },
            modifier = Modifier
                .fillMaxWidth()
                .testTag("product_detail.add_to_cart"),
        ) { Text("Add to cart") }
    }
}
