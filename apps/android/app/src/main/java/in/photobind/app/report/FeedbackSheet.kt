package `in`.photobind.app.report

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import `in`.photobind.app.ui.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Feedback / bug report form. Diagnostics are opt-in and readable first. */
@Composable
fun FeedbackSheet(initialKind: String = "feedback", prefill: String = "",
                  onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    val reporter = remember { Reporter(ctx) }
    val scope = rememberCoroutineScope()

    var kind by remember { mutableStateOf(initialKind) }
    var summary by remember { mutableStateOf(prefill) }
    var detail by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var includeDiag by remember { mutableStateOf(reporter.diagnosticsConsent) }
    var showDiag by remember { mutableStateOf(false) }
    var sending by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }

    Dialog(onDismissRequest = { if (!sending) onDismiss() }) {
        Surface(color = AppColors.bg, shape = RectangleShape) {
            Column(Modifier.fillMaxWidth().heightIn(max = 620.dp)
                .verticalScroll(rememberScrollState()).padding(MD),
                verticalArrangement = Arrangement.spacedBy(SM)) {

                Kicker("tell us")
                Text("Feedback or a bug?", style = MaterialTheme.typography.headlineMedium)

                Row(horizontalArrangement = Arrangement.spacedBy(SM)) {
                    listOf("feedback" to "Feedback", "bug" to "Bug").forEach { (v, label) ->
                        val selected = kind == v
                        if (selected) PrimaryButton(label, Modifier.weight(1f)) { kind = v }
                        else SecondaryButton(label, Modifier.weight(1f)) { kind = v }
                    }
                }

                Field("In one line, what happened?", summary, { summary = it },
                    placeholder = "Revoke didn't take effect")
                Column {
                    Text("Anything else — what you expected, what you saw",
                        fontSize = 12.sp, color = AppColors.ink.copy(alpha = .7f))
                    Spacer(Modifier.height(5.dp))
                    OutlinedTextField(
                        value = detail, onValueChange = { detail = it },
                        shape = RectangleShape, minLines = 3,
                        modifier = Modifier.fillMaxWidth(),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = Accent, unfocusedBorderColor = AppColors.line,
                            focusedContainerColor = AppColors.surface,
                            unfocusedContainerColor = AppColors.surface))
                }
                Field("Email, if you want a reply", email, { email = it },
                    placeholder = "optional")

                Row(Modifier.fillMaxWidth(), verticalAlignment =
                        androidx.compose.ui.Alignment.Top) {
                    Checkbox(checked = includeDiag, onCheckedChange = { includeDiag = it },
                        colors = CheckboxDefaults.colors(checkedColor = Accent,
                            checkmarkColor = MaterialTheme.colorScheme.onPrimary))
                    Column(Modifier.padding(top = 12.dp)) {
                        Text("Include a diagnostics report — app version, device, "
                            + "Android version, memory.", fontSize = 13.sp)
                        SecondaryButton(if (showDiag) "Hide" else "See exactly what") {
                            showDiag = !showDiag
                        }
                    }
                }
                if (showDiag) {
                    Text(reporter.diagnosticsPreview(),
                        fontFamily = Mono, fontSize = 10.sp, color = AppColors.muted,
                        modifier = Modifier.fillMaxWidth().heightIn(max = 180.dp)
                            .verticalScroll(rememberScrollState()))
                }
                Text("never included: your payload, your keys, or the part of a link after the #",
                    style = MaterialTheme.typography.labelSmall, color = AppColors.muted)

                if (error.isNotEmpty()) ErrorNote(error)

                Row(horizontalArrangement = Arrangement.spacedBy(SM)) {
                    SecondaryButton("Cancel", Modifier.weight(1f),
                        enabled = !sending) { onDismiss() }
                    PrimaryButton("Send", Modifier.weight(1f), loading = sending) {
                        if (summary.isBlank()) {
                            error = "Add one line about what happened so we can act on it."
                        } else {
                            error = ""; sending = true
                            reporter.diagnosticsConsent = includeDiag
                            scope.launch {
                                val delivery = withContext(Dispatchers.IO) {
                                    reporter.send(kind, summary, detail,
                                        email.ifBlank { null }, includeDiag)
                                }
                                sending = false
                                if (delivery == null) {
                                    error = "Couldn't reach the server. Check your connection and try again."
                                } else onDismiss()
                            }
                        }
                    }
                }
            }
        }
    }
}

/** Shown once when a crash from the previous run has been reported. */
@Composable
fun CrashReportedNotice(summary: String, onDetails: () -> Unit, onDismiss: () -> Unit) {
    Dialog(onDismissRequest = onDismiss) {
        Surface(color = AppColors.bg, shape = RectangleShape) {
            Column(Modifier.fillMaxWidth().padding(MD),
                verticalArrangement = Arrangement.spacedBy(SM)) {
                Kicker("previous session", Revoked)
                Text("The app closed unexpectedly", fontWeight = FontWeight.ExtraBold,
                    fontSize = 20.sp)
                Text("We've reported it so it can be fixed. Nothing you created was lost.",
                    style = MaterialTheme.typography.bodyMedium)
                Text(summary, fontFamily = Mono, fontSize = 10.sp, color = AppColors.muted)
                Row(horizontalArrangement = Arrangement.spacedBy(SM)) {
                    SecondaryButton("Add details", Modifier.weight(1f)) { onDetails() }
                    PrimaryButton("Close", Modifier.weight(1f)) { onDismiss() }
                }
            }
        }
    }
}
