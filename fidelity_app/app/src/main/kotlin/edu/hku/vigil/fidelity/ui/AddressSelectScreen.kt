package edu.hku.vigil.fidelity.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
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
import edu.hku.vigil.fidelity.AppState
import edu.hku.vigil.fidelity.data.Addresses
import edu.hku.vigil.fidelity.ui.components.ScreenMarker

@Composable
fun AddressSelectScreen(state: AppState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenMarker("address_select")
        Text(
            "Choose address",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.testTag("address_select.title"),
        )

        for (a in Addresses.all) {
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("address_select.address_card.${a.id}"),
            ) {
                Column(
                    modifier = Modifier.padding(14.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        a.label,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        a.line,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Button(
                        onClick = { state.selectAddress(a) },
                        modifier = Modifier
                            .fillMaxWidth()
                            .testTag("address_select.address.${a.id}"),
                    ) {
                        Text("Deliver here")
                    }
                }
            }
        }
    }
}
