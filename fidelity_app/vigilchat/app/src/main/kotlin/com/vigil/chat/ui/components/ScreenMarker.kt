package com.vigil.chat.ui.components

import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Single-authority screen marker. The composition tree must contain
 * exactly one node carrying [testTag] = "screen_marker" at any moment.
 *
 * The base screens compute whether a dialog is active and skip emitting
 * their own marker when the dialog will emit one instead.
 */
@Composable
fun ScreenMarker(stateId: String) {
    Text(
        text = "screen:$stateId",
        color = MaterialTheme.colorScheme.background,
        fontSize = 1.sp,
        lineHeight = 1.sp,
        modifier = Modifier
            .height(1.dp)
            .padding(0.dp)
            .testTag("screen_marker")
            .semantics { contentDescription = "screen:$stateId" },
    )
}
