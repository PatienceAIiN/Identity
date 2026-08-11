package `in`.photobind.app.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.ClickableText
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DarkMode
import androidx.compose.material.icons.filled.LightMode
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/* Zero-radius, flush-left, hairline rules. Every colour comes from the scheme so
   both themes work without a second set of components. */

@Composable
fun Kicker(text: String, color: Color = Blue) =
    Text(text, color = color, style = MaterialTheme.typography.labelSmall)

@Composable
fun SectionTitle(text: String, modifier: Modifier = Modifier) =
    Text(text, style = MaterialTheme.typography.labelSmall,
        color = AppColors.muted, modifier = modifier)

@Composable
fun Rule() = HorizontalDivider(thickness = 1.dp, color = AppColors.line)

/**
 * Expressive press feedback, by hand.
 *
 * Material 3 Expressive's own motion scheme is internal in material3 1.4, so the
 * behaviour it specifies is implemented here: a spring rather than a fixed
 * duration, so a press settles instead of stopping dead, and a corner that gives
 * under the finger and returns. Both are driven by the same interaction source
 * the button already owns, so they cannot drift out of step with its state.
 */
@Composable
fun rememberPressScale(interaction: MutableInteractionSource): Float {
    /*
     * Expressive press feedback, reduced to the part that cannot break.
     *
     * This animated a corner radius as well, and an under-damped spring
     * overshoots its target: releasing a button drove the radius slightly below
     * zero, and a negative corner throws while the frame is being drawn, which
     * killed the app. Clamping the value fixed that instance; removing the
     * animated shape removes the whole class, because a scale that overshoots is
     * merely a scale that overshoots — there is no illegal value to hit.
     */
    val pressed by interaction.collectIsPressedAsState()
    val scale by animateFloatAsState(
        targetValue = if (pressed) 0.97f else 1f,
        animationSpec = spring(dampingRatio = 0.55f, stiffness = 900f),
        label = "press-scale")
    return scale.coerceIn(0.5f, 1.5f)
}

@Composable
fun PrimaryButton(text: String, modifier: Modifier = Modifier,
                  enabled: Boolean = true, loading: Boolean = false,
                  onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    val scale = rememberPressScale(interaction)
    Button(onClick = onClick, enabled = enabled && !loading, shape = RectangleShape,
        interactionSource = interaction,
        colors = ButtonDefaults.buttonColors(
            containerColor = Accent,
            contentColor = MaterialTheme.colorScheme.onPrimary,
            disabledContainerColor = Accent.copy(alpha = .35f),
            disabledContentColor = MaterialTheme.colorScheme.onPrimary.copy(alpha = .6f)),
        modifier = modifier.heightIn(min = 52.dp)
            .graphicsLayer { scaleX = scale; scaleY = scale }) {
        if (loading) {
            CircularProgressIndicator(modifier = Modifier.size(15.dp), strokeWidth = 2.dp,
                color = MaterialTheme.colorScheme.onPrimary)
            Spacer(Modifier.width(SM))
        }
        Text(text, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 15.sp)
    }
}

@Composable
fun SecondaryButton(text: String, modifier: Modifier = Modifier,
                    color: Color = AppColors.ink, enabled: Boolean = true,
                    loading: Boolean = false, onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    val scale = rememberPressScale(interaction)
    OutlinedButton(onClick = onClick, enabled = enabled && !loading,
        interactionSource = interaction,
        shape = RectangleShape,
        border = androidx.compose.foundation.BorderStroke(1.dp, AppColors.line),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = color),
        modifier = modifier.heightIn(min = 52.dp)
            .graphicsLayer { scaleX = scale; scaleY = scale }) {
        if (loading) {
            CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp,
                color = color)
            Spacer(Modifier.width(SM))
        }
        Text(text, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 14.sp)
    }
}

@Composable
fun Field(label: String, value: String, onChange: (String) -> Unit,
          modifier: Modifier = Modifier, placeholder: String = "",
          password: Boolean = false, numeric: Boolean = false,
          singleLine: Boolean = true) {
    var revealed by remember { mutableStateOf(false) }
    Column(modifier.fillMaxWidth()) {
        Text(label, fontFamily = Mono, fontSize = 12.sp, color = AppColors.muted)
        Spacer(Modifier.height(6.dp))
        OutlinedTextField(
            value = value, onValueChange = onChange, singleLine = singleLine,
            shape = RectangleShape,
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
            placeholder = { Text(placeholder, fontSize = 14.sp, color = AppColors.muted) },
            visualTransformation = if (password && !revealed)
                PasswordVisualTransformation() else VisualTransformation.None,
            keyboardOptions = KeyboardOptions(
                keyboardType = when {
                    password -> KeyboardType.Password
                    numeric -> KeyboardType.NumberPassword
                    else -> KeyboardType.Text
                }),
            trailingIcon = if (!password) null else {
                {
                    IconButton(onClick = { revealed = !revealed }) {
                        Icon(
                            imageVector = if (revealed) Icons.Filled.VisibilityOff
                                          else Icons.Filled.Visibility,
                            contentDescription = if (revealed) "Hide password"
                                                 else "Show password",
                            tint = AppColors.ink)
                    }
                }
            },
            colors = OutlinedTextFieldDefaults.colors(
                focusedTextColor = AppColors.ink, unfocusedTextColor = AppColors.ink,
                focusedBorderColor = Accent, unfocusedBorderColor = AppColors.line,
                focusedContainerColor = AppColors.surface,
                unfocusedContainerColor = AppColors.surface,
                cursorColor = Accent),
        )
    }
}

@Composable
fun StateTag(state: String) {
    val (bg, ink, label) = when (state) {
        "active" -> Triple(Live.copy(alpha = .16f), Live, "Live")
        "revoked" -> Triple(Revoked.copy(alpha = .16f), Revoked, "Revoked")
        "expired" -> Triple(Expiring.copy(alpha = .20f), Expiring, "Expired")
        "scan_cap_reached" -> Triple(Revoked.copy(alpha = .16f), Revoked, "Scan cap")
        else -> Triple(AppColors.muted.copy(alpha = .16f), AppColors.muted, state)
    }
    Box(Modifier.background(bg).padding(horizontal = 10.dp, vertical = 4.dp)) {
        Text(label, color = ink, fontFamily = Mono, fontSize = 11.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
fun ErrorNote(text: String) {
    Row(Modifier.fillMaxWidth().background(Revoked.copy(alpha = .10f))
        .padding(SM), verticalAlignment = Alignment.Top) {
        Box(Modifier.width(3.dp).height(18.dp).background(Revoked))
        Spacer(Modifier.width(SM))
        Text(text, fontFamily = Mono, color = if (LocalThemeState.current?.isDark == true)
            Color(0xFFFF9A6D) else Color(0xFFB23C0F), fontSize = 13.sp)
    }
}

/** A dashboard number. Reads at a glance, which is the point of a tile. */
@Composable
fun StatTile(value: String, label: String, accent: Color = Accent,
             modifier: Modifier = Modifier, onClick: (() -> Unit)? = null) {
    Column(
        modifier
            .background(AppColors.surface)
            .then(if (onClick != null) Modifier.clickable { onClick() } else Modifier)
            .padding(MD),
        verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).background(accent))
            Spacer(Modifier.width(XS))
            Text(label.uppercase(), style = MaterialTheme.typography.labelSmall,
                color = AppColors.muted)
        }
        Text(value, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 28.sp,
            color = AppColors.ink, letterSpacing = (-1).sp)
    }
}

@Composable
fun Footer(modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().padding(top = MD, bottom = SM)) {
        Rule()
        Spacer(Modifier.height(SM))
        Text("Identity", fontFamily = Mono, fontWeight = FontWeight.Bold,
            fontSize = 15.sp, color = AppColors.ink)
        Text(LegalText.FOOTER, style = MaterialTheme.typography.labelSmall,
            color = AppColors.muted)
    }
}

/** Theme toggle: a sun that becomes a crescent. Same idea as the website. */
@Composable
fun ThemeToggle(modifier: Modifier = Modifier) {
    val theme = LocalThemeState.current ?: return
    // Google's own sun and moon, crossfading and turning into each other rather
    // than swapping instantly. Tint comes from the scheme so the icon is legible
    // in the theme it is currently sitting in — not the one it switches to.
    val raw by animateFloatAsState(if (theme.isDark) 1f else 0f, label = "theme")
    // Feeds an alpha, which must stay inside 0..1. This spec does not overshoot
    // today, but the crash above came from exactly this shape of assumption.
    val t = raw.coerceIn(0f, 1f)
    IconButton(onClick = { theme.toggle() }, modifier = modifier) {
        Box(contentAlignment = Alignment.Center) {
            Icon(Icons.Filled.LightMode, contentDescription = null,
                tint = AppColors.ink.copy(alpha = 1f - t),
                modifier = Modifier.size(22.dp).rotate(t * 90f))
            Icon(Icons.Filled.DarkMode, contentDescription = null,
                tint = AppColors.ink.copy(alpha = t),
                modifier = Modifier.size(22.dp).rotate((1f - t) * -90f))
        }
    }
}


/**
 * Consent row: a checkbox plus a sentence whose two document names are tappable
 * and highlighted, so the terms can be read without abandoning the form.
 */
@Composable
fun ConsentRow(checked: Boolean, onCheckedChange: (Boolean) -> Unit,
               onOpenTerms: () -> Unit, onOpenPrivacy: () -> Unit) {
    val TERMS = "terms"; val PRIVACY = "privacy"
    val text = buildAnnotatedString {
        append("I agree to the ")
        pushStringAnnotation(TERMS, TERMS)
        withStyle(SpanStyle(color = Accent, fontWeight = FontWeight.Bold,
            textDecoration = TextDecoration.Underline)) { append("terms of use") }
        pop()
        append(" and ")
        pushStringAnnotation(PRIVACY, PRIVACY)
        withStyle(SpanStyle(color = Accent, fontWeight = FontWeight.Bold,
            textDecoration = TextDecoration.Underline)) { append("privacy policy") }
        pop()
        append(".")
    }
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Checkbox(checked = checked, onCheckedChange = onCheckedChange,
            colors = CheckboxDefaults.colors(checkedColor = Accent,
                checkmarkColor = MaterialTheme.colorScheme.onPrimary,
                uncheckedColor = AppColors.muted))
        ClickableText(
            text = text,
            style = MaterialTheme.typography.bodyMedium.copy(color = AppColors.ink),
            onClick = { offset ->
                when {
                    text.getStringAnnotations(TERMS, offset, offset).isNotEmpty() -> onOpenTerms()
                    text.getStringAnnotations(PRIVACY, offset, offset).isNotEmpty() -> onOpenPrivacy()
                    else -> onCheckedChange(!checked)
                }
            })
    }
}

/**
 * Legal reader. A bottom sheet, not a centred dialog: it slides up from the
 * edge the thumb is nearest and can be dragged away, which is what a long
 * document wants on a phone.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LegalSheet(kind: String, onDismiss: () -> Unit) {
    val sections = if (kind == "terms") LegalText.TERMS else LegalText.PRIVACY
    val title = if (kind == "terms") "Terms of use" else "Privacy policy"
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        containerColor = AppColors.bg,
        shape = RectangleShape,
        dragHandle = { BottomSheetDefaults.DragHandle() },
    ) {
        Column(Modifier.fillMaxWidth().padding(horizontal = MD)
            .padding(bottom = MD)) {
            Text(title, style = MaterialTheme.typography.headlineMedium,
                color = AppColors.ink)
            Spacer(Modifier.height(XS))
            Text("Identity · a product of Patience AI · updated ${LegalText.UPDATED}",
                style = MaterialTheme.typography.labelSmall, color = AppColors.muted)
            Spacer(Modifier.height(SM))
            Rule()
            Column(Modifier.weight(1f, fill = false)
                .verticalScroll(rememberScrollState()).padding(vertical = SM)) {
                sections.forEach { (heading, body) ->
                    Text(heading, style = MaterialTheme.typography.titleLarge,
                        color = AppColors.ink,
                        modifier = Modifier.padding(top = SM, bottom = XS))
                    Text(body, style = MaterialTheme.typography.bodyMedium,
                        color = AppColors.ink)
                }
            }
            Rule()
            Spacer(Modifier.height(SM))
            PrimaryButton("Close", Modifier.fillMaxWidth()) { onDismiss() }
        }
    }
}

/** Confirmation dialog. Destructive actions can demand a typed word. */
@Composable
fun ConfirmDialog(title: String, body: String, confirmLabel: String = "Confirm",
                  cancelLabel: String = "Cancel", destructive: Boolean = false,
                  requireText: String? = null, busy: Boolean = false,
                  onConfirm: () -> Unit, onDismiss: () -> Unit) {
    var typed by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        shape = RectangleShape,
        containerColor = AppColors.bg,
        title = { Text(title, style = MaterialTheme.typography.headlineMedium,
            color = AppColors.ink) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(SM)) {
                Text(body, style = MaterialTheme.typography.bodyMedium,
                    color = AppColors.ink)
                if (requireText != null) {
                    Field("Type $requireText to confirm", typed, { typed = it })
                }
            }
        },
        confirmButton = {
            PrimaryButton(confirmLabel, loading = busy,
                enabled = requireText == null || typed == requireText) { onConfirm() }
        },
        dismissButton = {
            SecondaryButton(cancelLabel, enabled = !busy) { onDismiss() }
        },
    )
}
