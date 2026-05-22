package edu.hku.vigil.fidelity.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import edu.hku.vigil.fidelity.AppState
import edu.hku.vigil.fidelity.Screen
import edu.hku.vigil.fidelity.data.Catalog
import edu.hku.vigil.fidelity.ui.components.cents
import edu.hku.vigil.fidelity.ui.components.ScreenMarker

@Composable
fun HomeScreen(state: AppState) {
    val featured = Catalog.products.first()
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        ScreenMarker("home")

        Text(
            "Morning menu",
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("home.title"),
        )
        Text(
            "Deterministic coffee, seeded data, stable UI markers.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag("home.subtitle"),
        )

        ElevatedCard(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("home.featured_card"),
        ) {
            Column(
                modifier = Modifier.padding(18.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    "Featured",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                )
                Text(
                    featured.name,
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.testTag("home.featured_name"),
                )
                Text(
                    "${cents(featured.priceCents)} · ${featured.description}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Button(
                    onClick = { state.openProductDetail(featured) },
                    modifier = Modifier.testTag("home.featured_open"),
                ) { Text("View espresso") }
            }
        }

        Card(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("home.quick_actions"),
        ) {
            Column(
                modifier = Modifier.padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    "Quick actions",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        onClick = { state.navigate(Screen.SEARCH) },
                        modifier = Modifier.testTag("home.open_search"),
                    ) { Text("Search") }

                    Button(
                        onClick = { state.navigate(Screen.CATALOG) },
                        modifier = Modifier.testTag("home.open_catalog"),
                    ) { Text("Catalog") }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(
                        onClick = { state.openCart() },
                        modifier = Modifier.testTag("home.open_cart"),
                    ) { Text("Cart") }

                    OutlinedButton(
                        onClick = { state.navigate(Screen.ORDERS) },
                        modifier = Modifier.testTag("home.open_orders"),
                    ) { Text("Orders") }

                    OutlinedButton(
                        onClick = { state.navigate(Screen.SETTINGS) },
                        modifier = Modifier.testTag("home.open_settings"),
                    ) { Text("Settings") }
                }
            }
        }
    }
}
