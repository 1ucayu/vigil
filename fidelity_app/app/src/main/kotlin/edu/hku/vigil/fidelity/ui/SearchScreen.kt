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
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import edu.hku.vigil.fidelity.AppState
import edu.hku.vigil.fidelity.Screen
import edu.hku.vigil.fidelity.data.Catalog
import edu.hku.vigil.fidelity.ui.components.ScreenMarker
import edu.hku.vigil.fidelity.ui.components.cents

@Composable
fun SearchScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("search")
        Text(
            "Find a drink",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("search.title"),
        )

        OutlinedTextField(
            value = state.searchQuery,
            onValueChange = { state.updateSearchQuery(it) },
            label = { Text("Search catalog") },
            modifier = Modifier
                .fillMaxWidth()
                .testTag("search.query"),
        )

        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Button(
                onClick = { state.navigate(Screen.HOME) },
                modifier = Modifier.testTag("search.back_home"),
            ) { Text("Home") }
        }

        val results = Catalog.search(state.searchQuery)
        LazyColumn(
            modifier = Modifier.testTag("search.results"),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(results, key = { it.id }) { p ->
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag("search.result_card.${p.id}"),
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
                            )
                            Text(
                                cents(p.priceCents),
                                style = MaterialTheme.typography.labelLarge,
                                color = MaterialTheme.colorScheme.primary,
                            )
                        }
                        Button(
                            onClick = { state.openProductDetail(p) },
                            modifier = Modifier.testTag("search.result_row.${p.id}"),
                        ) { Text("Open") }
                    }
                }
            }
        }
    }
}
