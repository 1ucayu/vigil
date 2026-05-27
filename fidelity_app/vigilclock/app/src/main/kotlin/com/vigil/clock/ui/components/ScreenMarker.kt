package com.vigil.clock.ui.components

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
 * Semantically readable screen marker. Visually tiny so the app still feels real,
 * while UIAutomator can fingerprint state via text, resource-id, or
 * contentDescription. Lives in its own composable so that volatile readouts
 * (timer.remaining, stopwatch.elapsed) do not perturb the screen fingerprint.
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
