package com.vigil.market.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val VigilMarketColors = lightColorScheme(
    primary = Color(0xFF006A67),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFB8EFEA),
    onPrimaryContainer = Color(0xFF00201F),
    secondary = Color(0xFFB15D3A),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFFFDBCC),
    onSecondaryContainer = Color(0xFF3A1608),
    tertiary = Color(0xFF6750A4),
    onTertiary = Color.White,
    background = Color(0xFFF7F8F5),
    onBackground = Color(0xFF181C1B),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF181C1B),
    surfaceVariant = Color(0xFFE1E4DF),
    onSurfaceVariant = Color(0xFF424846),
    outline = Color(0xFF727876),
)

@Composable
fun VigilMarketTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = VigilMarketColors,
        content = content,
    )
}
