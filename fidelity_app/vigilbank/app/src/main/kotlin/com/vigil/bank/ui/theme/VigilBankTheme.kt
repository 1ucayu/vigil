package com.vigil.bank.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val VigilBankColors = lightColorScheme(
    primary = Color(0xFF0B3D91),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD7E3FF),
    onPrimaryContainer = Color(0xFF001A4D),
    secondary = Color(0xFF2E7D32),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFB6F2BD),
    onSecondaryContainer = Color(0xFF062611),
    tertiary = Color(0xFF455A64),
    onTertiary = Color.White,
    background = Color(0xFFF5F7FB),
    onBackground = Color(0xFF101418),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF101418),
    surfaceVariant = Color(0xFFE2E6EE),
    onSurfaceVariant = Color(0xFF424A55),
    outline = Color(0xFF6F7782),
    error = Color(0xFFB3261E),
    onError = Color.White,
)

@Composable
fun VigilBankTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = VigilBankColors,
        content = content,
    )
}
