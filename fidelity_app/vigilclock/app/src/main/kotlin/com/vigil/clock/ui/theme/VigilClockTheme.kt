package com.vigil.clock.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * VigilClock palette: deep midnight blue surfaces with an amber accent. Deliberately
 * different from VigilMarket so screenshots are visually distinguishable.
 */
private val VigilClockColors = darkColorScheme(
    primary = Color(0xFFFFB300),
    onPrimary = Color(0xFF1A1300),
    primaryContainer = Color(0xFF553F00),
    onPrimaryContainer = Color(0xFFFFDF9C),
    secondary = Color(0xFF6FB7FF),
    onSecondary = Color(0xFF002744),
    secondaryContainer = Color(0xFF003C66),
    onSecondaryContainer = Color(0xFFCFE4FF),
    tertiary = Color(0xFFE0BBE4),
    onTertiary = Color(0xFF2D1737),
    background = Color(0xFF0B1220),
    onBackground = Color(0xFFE3E8F2),
    surface = Color(0xFF111A2E),
    onSurface = Color(0xFFE3E8F2),
    surfaceVariant = Color(0xFF1B2540),
    onSurfaceVariant = Color(0xFFB6C2D9),
    outline = Color(0xFF6A7796),
)

@Composable
fun VigilClockTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = VigilClockColors,
        content = content,
    )
}
