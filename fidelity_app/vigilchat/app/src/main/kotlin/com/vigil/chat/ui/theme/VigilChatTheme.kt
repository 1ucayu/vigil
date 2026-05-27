package com.vigil.chat.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val VigilChatColors = lightColorScheme(
    primary = Color(0xFF6A4DBC),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE6DEFF),
    onPrimaryContainer = Color(0xFF21005D),
    secondary = Color(0xFF008B8B),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFB2EBE7),
    onSecondaryContainer = Color(0xFF00201E),
    tertiary = Color(0xFFB55B82),
    onTertiary = Color.White,
    background = Color(0xFFF7F4FB),
    onBackground = Color(0xFF1B1B1F),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF1B1B1F),
    surfaceVariant = Color(0xFFE4E1EC),
    onSurfaceVariant = Color(0xFF47464F),
    outline = Color(0xFF777680),
)

@Composable
fun VigilChatTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = VigilChatColors,
        content = content,
    )
}
