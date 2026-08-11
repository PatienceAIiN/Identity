package `in`.photobind.app.ota

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import `in`.photobind.app.ui.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Update prompt. Downloading happens off the UI thread with visible progress;
 * an optional update can be dismissed, a mandatory one cannot.
 */
@Composable
fun UpdateSheet(status: UpdateManager.Status, onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    val manager = remember { UpdateManager(ctx) }
    val scope = rememberCoroutineScope()
    var progress by remember { mutableStateOf(-1f) }   // -1 = not started
    var error by remember { mutableStateOf("") }

    val update = when (status) {
        is UpdateManager.Status.Available -> status.update
        is UpdateManager.Status.Unsupported -> status.update
        else -> return
    }
    val unsupported = status is UpdateManager.Status.Unsupported
    val blocking = update.mandatory && !unsupported

    Dialog(onDismissRequest = { if (!blocking && progress < 0f) onDismiss() }) {
        Surface(color = AppColors.bg, shape = RectangleShape) {
            Column(Modifier.fillMaxWidth().padding(MD),
                verticalArrangement = Arrangement.spacedBy(SM)) {
                Kicker(if (unsupported) "update available" else "new version")
                Text(
                    if (unsupported) "This update needs a newer Android"
                    else "Identity ${update.versionName}",
                    style = MaterialTheme.typography.headlineMedium)

                if (unsupported) {
                    Text("Version ${update.versionName} requires " +
                        "${update.minAndroid} or newer. This phone runs an older " +
                        "release, so it will keep working on the version you have.",
                        style = MaterialTheme.typography.bodyMedium)
                } else {
                    if (update.notes.isNotBlank()) {
                        Text(update.notes, style = MaterialTheme.typography.bodyMedium)
                    }
                    Text("${"%.1f".format(update.sizeBytes / 1_048_576.0)} MB · " +
                        "needs ${update.minAndroid} or newer",
                        style = MaterialTheme.typography.labelSmall, color = AppColors.muted)
                }

                if (progress in 0f..1f) {
                    LinearProgressIndicator(
                        progress = progress,
                        modifier = Modifier.fillMaxWidth().height(6.dp),
                        color = Accent, trackColor = AppColors.surface)
                    Text("downloading ${(progress * 100).toInt()}% — " +
                        "you can keep using the app",
                        style = MaterialTheme.typography.labelSmall, color = AppColors.muted)
                }
                if (error.isNotEmpty()) ErrorNote(error)

                Row(horizontalArrangement = Arrangement.spacedBy(SM)) {
                    if (!unsupported && !manager.canInstallInApp) {
                        // This build cannot install for itself; the browser does
                        // it. Say so rather than appearing to stall.
                        PrimaryButton("Get the update", Modifier.weight(1f)) {
                            manager.openInBrowser(update)
                            onDismiss()
                        }
                    } else if (!unsupported) {
                        PrimaryButton(
                            if (progress >= 1f) "Install" else "Update now",
                            Modifier.weight(1f),
                            loading = progress in 0f..0.999f,
                        ) {
                            error = ""
                            scope.launch {
                                progress = 0f
                                val result = withContext(Dispatchers.IO) {
                                    manager.download(update) { p -> progress = p }
                                }
                                result.onSuccess { file ->
                                    progress = 1f
                                    manager.install(file)
                                }.onFailure {
                                    progress = -1f
                                    error = it.message
                                        ?: "The download failed. Check your connection and try again."
                                }
                            }
                        }
                    }
                    if (!blocking) {
                        SecondaryButton(if (unsupported) "Got it" else "Later",
                            Modifier.weight(1f), enabled = progress < 0f) { onDismiss() }
                    }
                }
            }
        }
    }
}
