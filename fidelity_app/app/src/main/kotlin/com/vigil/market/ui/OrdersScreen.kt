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
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.vigil.market.AppState
import com.vigil.market.Screen
import com.vigil.market.data.Orders
import com.vigil.market.ui.components.cents
import com.vigil.market.ui.components.ScreenMarker

@Composable
fun OrdersScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("orders")
        Text(
            "Past orders",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("orders.title"),
        )

        for (o in Orders.past) {
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("orders.row.${o.id}"),
            ) {
                Row(
                    modifier = Modifier.padding(14.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Text(
                        "${o.id}  ${o.productName} x${o.qty}  ${cents(o.totalCents)}  -> ${o.addressLabel}",
                        style = MaterialTheme.typography.bodyLarge,
                        modifier = Modifier.testTag("orders.row.${o.id}.summary"),
                    )
                }
            }
        }

        Button(
            onClick = { state.navigate(Screen.HOME) },
            modifier = Modifier
                .fillMaxWidth()
                .testTag("orders.back_home"),
        ) { Text("Home") }
    }
}
