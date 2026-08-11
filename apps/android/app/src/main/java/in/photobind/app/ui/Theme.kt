package `in`.photobind.app.ui

import android.content.Context
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.Color
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Colours come from Tokens.kt, generated from packages/tokens/tokens.json — the
 * same source the web build uses, so the two cannot drift.
 *
 * Light is the default after install. Dark is a deliberate choice, remembered
 * across launches. Every surface reads its colour from the scheme rather than a
 * hardcoded token, which is what was wrong before: dark mode painted dark text
 * on dark panels because half the app referenced the light values directly.
 */

// ── light ─────────────────────────────────────────────────────────────────
val LightBg = Color(Tokens.bg)              // white
val LightSurface = Color(Tokens.surface)
val LightInk = Color(Tokens.ink)
val LightMuted = Color(Tokens.muted)
val LightLine = Color(0x33201E1D)

// ── dark ──────────────────────────────────────────────────────────────────
val DarkBg = Color(0xFF17161C)
val DarkSurface = Color(0xFF201E26)
val DarkInk = Color(0xFFECE9F2)
val DarkMuted = Color(0xFF9B96A6)
val DarkLine = Color(0x38ECE9F2)

// ── constant across themes: state colours carry meaning, not decoration ───
val Accent = Color(Tokens.accent)
val Blue = Color(Tokens.blue)
val Live = Color(Tokens.live)
val Revoked = Color(Tokens.revoked)
val Expiring = Color(Tokens.expiring)

enum class ThemeMode { Light, Dark }

/** Remembered theme choice. Light unless the person picked dark. */
class ThemeState(private val context: Context) {
    private val prefs = context.getSharedPreferences("ui", Context.MODE_PRIVATE)
    var mode by mutableStateOf(
        if (prefs.getString("theme", "light") == "dark") ThemeMode.Dark else ThemeMode.Light)
        private set

    fun toggle() {
        mode = if (mode == ThemeMode.Dark) ThemeMode.Light else ThemeMode.Dark
        prefs.edit().putString("theme",
            if (mode == ThemeMode.Dark) "dark" else "light").apply()
    }

    val isDark: Boolean get() = mode == ThemeMode.Dark
}

val LocalThemeState = compositionLocalOf<ThemeState?> { null }

/** Theme-aware aliases every screen uses instead of naming a colour. */
object AppColors {
    val bg: Color @Composable get() = MaterialTheme.colorScheme.background
    val surface: Color @Composable get() = MaterialTheme.colorScheme.surface
    val ink: Color @Composable get() = MaterialTheme.colorScheme.onBackground
    val muted: Color @Composable get() = MaterialTheme.colorScheme.onSurfaceVariant
    val line: Color @Composable get() = MaterialTheme.colorScheme.outlineVariant
}

// Module grid: 6 / 12 / 24 / 42 / 84.
val XS = Tokens.space_xs.dp
val SM = Tokens.space_sm.dp
val MD = Tokens.space_md.dp
val LG = Tokens.space_lg.dp
val XL = Tokens.space_xl.dp

/** Courier where it exists, the platform monospace otherwise — the same
 *  intent as the web build's "Courier New", Courier, monospace stack. */
val Mono = FontFamily(
    androidx.compose.ui.text.font.Font(
        androidx.compose.ui.text.font.DeviceFontFamilyName("Courier New"),
        weight = FontWeight.Normal),
    androidx.compose.ui.text.font.Font(
        androidx.compose.ui.text.font.DeviceFontFamilyName("Courier New"),
        weight = FontWeight.Bold),
    androidx.compose.ui.text.font.Font(
        androidx.compose.ui.text.font.DeviceFontFamilyName("monospace"),
        weight = FontWeight.Normal),
)

private val typography = Typography(
    headlineLarge = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Bold,
        fontSize = 26.sp, lineHeight = 32.sp, letterSpacing = (-0.5).sp),
    headlineMedium = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Bold,
        fontSize = 20.sp, lineHeight = 26.sp, letterSpacing = (-0.3).sp),
    titleLarge = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Bold,
        fontSize = 16.sp, lineHeight = 22.sp),
    bodyLarge = TextStyle(fontFamily = Mono, fontSize = 14.sp, lineHeight = 22.sp),
    bodyMedium = TextStyle(fontFamily = Mono, fontSize = 13.sp, lineHeight = 20.sp),
    labelSmall = TextStyle(fontFamily = Mono, fontSize = 11.sp, lineHeight = 16.sp),
    labelLarge = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Bold,
        fontSize = 14.sp),
)

/**
 * Every slot is filled deliberately, both themes.
 *
 * This is not thoroughness for its own sake. A slot left unset falls back to
 * Material's baseline palette, which is lavender — and components read those
 * slots whether or not we do. `OutlinedTextField` fills itself from
 * `surfaceContainerHighest`, and `Surface` blends `surfaceTint` over its colour
 * at any non-zero elevation. That is where the lavender panels and lavender
 * field fills came from: our six overrides were correct and simply weren't the
 * colours being drawn.
 *
 * `surfaceTint` is transparent on purpose. This design is flat — elevation is
 * shown with a rule, never with a tinted overlay — so an overlay could only ever
 * shift a colour away from the one we chose.
 */
@Composable
fun IdentityTheme(themeState: ThemeState? = null, content: @Composable () -> Unit) {
    val dark = themeState?.isDark ?: false
    val scheme = if (dark) darkColorScheme(
        primary = Accent, onPrimary = Color(0xFF17161C),
        primaryContainer = Accent, onPrimaryContainer = Color(0xFF17161C),
        inversePrimary = Accent,
        secondary = DarkInk, onSecondary = DarkBg,
        secondaryContainer = DarkSurface, onSecondaryContainer = DarkInk,
        tertiary = Blue, onTertiary = Color.White,
        tertiaryContainer = DarkSurface, onTertiaryContainer = DarkInk,
        background = DarkBg, onBackground = DarkInk,
        surface = DarkSurface, onSurface = DarkInk,
        surfaceVariant = DarkSurface, onSurfaceVariant = DarkMuted,
        surfaceTint = Color.Transparent,
        surfaceBright = Color(0xFF33303A), surfaceDim = Color(0xFF121116),
        surfaceContainerLowest = Color(0xFF121116),
        surfaceContainerLow = Color(0xFF1B1A21),
        surfaceContainer = DarkSurface,
        surfaceContainerHigh = Color(0xFF29272F),
        surfaceContainerHighest = Color(0xFF33303A),
        inverseSurface = DarkInk, inverseOnSurface = DarkBg,
        outline = DarkLine, outlineVariant = DarkLine,
        error = Revoked, onError = Color.White,
        errorContainer = DarkSurface, onErrorContainer = Color(0xFFFF9A6D),
        scrim = Color.Black,
    ) else lightColorScheme(
        primary = Accent, onPrimary = LightInk,
        primaryContainer = Accent, onPrimaryContainer = LightInk,
        inversePrimary = Accent,
        secondary = LightInk, onSecondary = LightBg,
        secondaryContainer = LightSurface, onSecondaryContainer = LightInk,
        tertiary = Blue, onTertiary = Color.White,
        tertiaryContainer = LightSurface, onTertiaryContainer = LightInk,
        background = LightBg, onBackground = LightInk,
        surface = LightSurface, onSurface = LightInk,
        surfaceVariant = LightSurface, onSurfaceVariant = LightMuted,
        surfaceTint = Color.Transparent,
        surfaceBright = LightBg, surfaceDim = Color(0xFFE0DFDF),
        surfaceContainerLowest = LightBg,
        surfaceContainerLow = Color(0xFFFAFAFA),
        surfaceContainer = LightSurface,
        surfaceContainerHigh = Color(0xFFEDECEC),
        surfaceContainerHighest = Color(0xFFE6E5E5),
        inverseSurface = LightInk, inverseOnSurface = Color(Tokens.inkInverse),
        outline = LightLine, outlineVariant = LightLine,
        error = Revoked, onError = Color.White,
        errorContainer = Color(0xFFFBE7DE), onErrorContainer = Color(0xFFB23C0F),
        scrim = Color.Black,
    )
    CompositionLocalProvider(LocalThemeState provides themeState) {
        // Material 3, 1.4 line. The Expressive entry points in this release
        // (MaterialExpressiveTheme, MotionScheme.expressive(), ButtonGroup,
        // LoadingIndicator) are marked internal and only become public API in
        // material3 1.5.0-alpha, which requires compiling against SDK 37 — so
        // the expressive behaviour this app needs is implemented directly in
        // Components.kt: springs rather than fixed curves, and shape and size
        // that respond to a press.
        MaterialTheme(
            colorScheme = scheme,
            typography = typography,
            shapes = Shapes(
                extraSmall = RoundedCornerShape(0.dp), small = RoundedCornerShape(0.dp),
                medium = RoundedCornerShape(0.dp), large = RoundedCornerShape(0.dp),
                extraLarge = RoundedCornerShape(0.dp),
            ),
            content = content,
        )
    }
}
