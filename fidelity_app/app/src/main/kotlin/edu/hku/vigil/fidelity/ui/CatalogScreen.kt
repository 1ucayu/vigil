package edu.hku.vigil.fidelity.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import edu.hku.vigil.fidelity.AppState
import edu.hku.vigil.fidelity.data.Catalog
import edu.hku.vigil.fidelity.ui.components.ScreenMarker
import edu.hku.vigil.fidelity.ui.components.cents

@Composable
fun CatalogScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("catalog")
        Text(
            "Catalog",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("catalog.title"),
        )
        Text(
            "A fixed product set with repeated card structure for template matching.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag("catalog.subtitle"),
        )

        LazyColumn(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("catalog.list"),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(Catalog.products, key = { it.id }) { p ->
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag("catalog.product_row.${p.id}"),
                ) {
                    Row(
                        modifier = Modifier.padding(14.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Column(
                            modifier = Modifier.weight(1f),
                            verticalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            Text(
                                p.name,
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold,
                                modifier = Modifier.testTag("catalog.product_row.${p.id}.name"),
                            )
                            Text(
                                p.description,
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.testTag("catalog.product_row.${p.id}.description"),
                            )
                            Text(
                                cents(p.priceCents),
                                style = MaterialTheme.typography.labelLarge,
                                color = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.testTag("catalog.product_row.${p.id}.price"),
                            )
                        }
                        Button(
                            onClick = { state.openProductDetail(p) },
                            modifier = Modifier.testTag("catalog.product_row.${p.id}.open"),
                        ) {
                            Text("View")
                        }
                    }
                }
            }
        }
    }
}
