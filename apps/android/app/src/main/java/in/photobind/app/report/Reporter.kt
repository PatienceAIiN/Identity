package `in`.photobind.app.report

import android.content.Context
import android.os.Build
import `in`.photobind.app.BuildConfig
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.PrintWriter
import java.io.StringWriter

/**
 * Feedback, bug reports, and automatic crash reporting.
 *
 * Two rules, same as the web client:
 * - Diagnostics are opt-in, and the exact text is shown before sending.
 * - Nothing that could carry a payload key is ever collected. There is no
 *   code path here that reads a share link or its fragment.
 *
 * A crash is written to disk by the uncaught-exception handler (the process is
 * dying and a network call would not finish), then sent on next launch and the
 * person is told it happened.
 */
class Reporter(private val context: Context) {

    private val http = OkHttpClient()
    private val json = "application/json".toMediaType()
    private val prefs = context.getSharedPreferences("reports", Context.MODE_PRIVATE)
    private val pendingFile = java.io.File(context.filesDir, "pending-crash.txt")

    companion object {
        private const val CONSENT = "diagnostics_consent"
    }

    var diagnosticsConsent: Boolean
        get() = prefs.getBoolean(CONSENT, false)
        set(v) = prefs.edit().putBoolean(CONSENT, v).apply()

    /** Exactly what would be attached, as shown to the person. */
    fun diagnostics(extra: Map<String, String> = emptyMap()): JSONObject {
        val runtime = Runtime.getRuntime()
        return JSONObject().apply {
            put("platform", "android")
            put("app_version", BuildConfig.VERSION_NAME)
            put("version_code", BuildConfig.VERSION_CODE)
            put("android_release", Build.VERSION.RELEASE)
            put("sdk_int", Build.VERSION.SDK_INT)
            put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
            put("abi", Build.SUPPORTED_ABIS.joinToString(",").take(60))
            put("locale", context.resources.configuration.locales[0].toLanguageTag())
            put("memory_used_mb", (runtime.totalMemory() - runtime.freeMemory()) / 1_048_576)
            put("memory_max_mb", runtime.maxMemory() / 1_048_576)
            put("api_base", BuildConfig.API_BASE)
            extra.forEach { (k, v) -> put(k, v) }
        }
    }

    fun diagnosticsPreview(): String = diagnostics().toString(2)

    /** Returns the server's delivery status, or null if the call failed. */
    fun send(kind: String, summary: String, detail: String,
             reporterEmail: String?, includeDiagnostics: Boolean): String? {
        val body = JSONObject().apply {
            put("kind", kind)
            put("summary", summary.take(300))
            put("detail", detail.take(8000))
            put("platform", "android")
            put("app_version", BuildConfig.VERSION_NAME)
            if (!reporterEmail.isNullOrBlank()) put("reporter_email", reporterEmail)
            put("include_diagnostics", includeDiagnostics)
            if (includeDiagnostics) put("diagnostics", diagnostics())
        }
        return runCatching {
            http.newCall(Request.Builder()
                .url("${BuildConfig.API_BASE}/v1/reports")
                .post(body.toString().toRequestBody(json)).build())
                .execute().use { r ->
                    if (!r.isSuccessful) return null
                    JSONObject(r.body?.string().orEmpty()).optString("delivery")
                }
        }.getOrNull()
    }

    // -- crash handling -------------------------------------------------------

    /** Installs the handler. The previous handler still runs, so the system
     *  crash dialog and any other reporter behave as before. */
    fun installCrashHandler() {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            runCatching { persistCrash(thread, error) }
            previous?.uncaughtException(thread, error)
        }
    }

    private fun persistCrash(thread: Thread, error: Throwable) {
        val sw = StringWriter()
        error.printStackTrace(PrintWriter(sw))
        pendingFile.writeText(
            "thread: ${thread.name}\n" +
            "type: ${error.javaClass.name}\n" +
            "message: ${error.message}\n\n" +
            sw.toString().take(6000))
    }

    fun hasPendingCrash(): Boolean = pendingFile.exists()

    /**
     * Sends a crash saved by a previous run. Diagnostics ride along only if the
     * person already agreed to share them. Returns the crash summary so the UI
     * can tell them it was reported, rather than doing it silently.
     */
    fun flushPendingCrash(): String? {
        if (!pendingFile.exists()) return null
        val detail = runCatching { pendingFile.readText() }.getOrNull() ?: return null
        val type = detail.lineSequence().firstOrNull { it.startsWith("type: ") }
            ?.removePrefix("type: ") ?: "crash"
        val message = detail.lineSequence().firstOrNull { it.startsWith("message: ") }
            ?.removePrefix("message: ")?.take(120) ?: ""
        val summary = "android crash: $type${if (message.isBlank()) "" else " — $message"}"
        send("crash", summary, detail, null, diagnosticsConsent)
        pendingFile.delete()
        return summary
    }
}
