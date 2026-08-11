package `in`.photobind.app.ota

import android.content.Context
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageInstaller
import `in`.photobind.app.MainActivity
import android.os.Build
import androidx.core.content.FileProvider
import `in`.photobind.app.BuildConfig
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

/**
 * Over-the-air updates.
 *
 * check()     — reads the manifest; returns an Update only if the server's
 *               versionCode is higher AND this device's Android version is
 *               supported. A device below minSdk is told plainly, never
 *               handed an APK it cannot install.
 * download()  — streams the APK to app-specific cache with progress, then
 *               verifies SHA-256 against the manifest. A mismatch deletes the
 *               file and fails: we never hand an unverified binary to the
 *               installer.
 * install()   — hands the verified file to the system installer.
 * onInstalled()— called on next launch when versionCode has advanced: reports
 *               the install to the server (which prunes superseded APKs from
 *               R2) and deletes every downloaded APK from the device.
 */
class UpdateManager(private val context: Context) {

    data class Update(
        val versionCode: Int,
        val versionName: String,
        val sizeBytes: Long,
        val sha256: String,
        val url: String,
        val notes: String,
        val mandatory: Boolean,
        val minSdk: Int,
        val minAndroid: String,
    )

    sealed interface Status {
        data object UpToDate : Status
        data class Available(val update: Update) : Status
        data class Unsupported(val update: Update) : Status   // device too old
        data class Failed(val reason: String) : Status
    }

    private val http = OkHttpClient()
    private val prefs = context.getSharedPreferences("ota", Context.MODE_PRIVATE)
    private val apkDir = File(context.cacheDir, "updates").apply { mkdirs() }

    fun check(): Status {
        val req = Request.Builder().url("${BuildConfig.API_BASE}/v1/app/latest").build()
        val body = runCatching {
            http.newCall(req).execute().use { r ->
                if (!r.isSuccessful) return Status.Failed("server said ${r.code}")
                r.body?.string().orEmpty()
            }
        }.getOrElse { return Status.Failed("couldn't reach the update server") }

        val j = runCatching { JSONObject(body) }
            .getOrElse { return Status.Failed("bad manifest") }
        if (!j.optBoolean("available")) return Status.UpToDate

        val update = Update(
            versionCode = j.getInt("version_code"),
            versionName = j.getString("version_name"),
            sizeBytes = j.optLong("size_bytes"),
            sha256 = j.getString("sha256"),
            url = j.getString("url"),
            notes = j.optString("notes"),
            mandatory = j.optBoolean("mandatory"),
            minSdk = j.optInt("min_sdk", 26),
            minAndroid = j.optString("min_android", "Android 8.0"),
        )
        return when {
            update.versionCode <= BuildConfig.VERSION_CODE -> Status.UpToDate
            Build.VERSION.SDK_INT < update.minSdk -> Status.Unsupported(update)
            else -> Status.Available(update)
        }
    }

    /** Streams to cache with progress callbacks; verifies the digest. */
    fun download(update: Update, onProgress: (Float) -> Unit): Result<File> {
        val target = File(apkDir, "identity-${update.versionName}-${update.versionCode}.apk")
        if (target.exists() && sha256Of(target) == update.sha256) return Result.success(target)

        val req = Request.Builder().url(update.url).build()
        return runCatching {
            http.newCall(req).execute().use { res ->
                if (!res.isSuccessful) error("download failed (${res.code})")
                val total = res.body?.contentLength()?.takeIf { it > 0 } ?: update.sizeBytes
                res.body!!.byteStream().use { input ->
                    target.outputStream().use { out ->
                        val buf = ByteArray(64 * 1024)
                        var read: Int
                        var done = 0L
                        while (input.read(buf).also { read = it } > 0) {
                            out.write(buf, 0, read)
                            done += read
                            if (total > 0) onProgress((done.toFloat() / total).coerceIn(0f, 1f))
                        }
                    }
                }
            }
            val actual = sha256Of(target)
            if (actual != update.sha256) {
                target.delete()
                error("the downloaded file didn't match its checksum — nothing was installed")
            }
            prefs.edit().putInt("pending_version", update.versionCode).apply()
            target
        }
    }

    /** True when this build can install an update itself. */
    val canInstallInApp: Boolean get() = BuildConfig.OTA_IN_APP

    /**
     * Install through PackageInstaller sessions rather than by handing a
     * downloaded file to the generic installer with ACTION_VIEW.
     *
     * This is the sanctioned path for an app that updates itself, and it is a
     * narrower one: the bytes are streamed into a session owned by this app for
     * this package, instead of a world-readable content URI pointed at whatever
     * will accept an APK mime type. It does not remove Play Protect's warning on
     * its own — an app that downloads and installs an APK is the shape of thing
     * that warning exists for — but it is the correct API, and the file never
     * becomes reachable by another app.
     *
     * Falls back to the old intent if a session cannot be opened, so a failure
     * here still leaves a way to update.
     */
    fun install(apk: File) {
        runCatching {
            val installer = context.packageManager.packageInstaller
            val params = PackageInstaller.SessionParams(
                PackageInstaller.SessionParams.MODE_FULL_INSTALL)
            params.setAppPackageName(context.packageName)
            val sessionId = installer.createSession(params)
            installer.openSession(sessionId).use { session ->
                session.openWrite("identity", 0, apk.length()).use { out ->
                    apk.inputStream().use { it.copyTo(out) }
                    session.fsync(out)
                }
                val intent = Intent(context, MainActivity::class.java)
                    .setAction("in.photobind.app.INSTALL_RESULT")
                val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                    PendingIntent.FLAG_MUTABLE
                val pending = PendingIntent.getActivity(context, sessionId, intent, flags)
                session.commit(pending.intentSender)
            }
        }.onFailure {
            val uri = FileProvider.getUriForFile(
                context, "${context.packageName}.updates", apk)
            context.startActivity(Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or
                         Intent.FLAG_ACTIVITY_NEW_TASK)
            })
        }
    }

    /**
     * Update path for builds without REQUEST_INSTALL_PACKAGES: hand the
     * download to the browser, which has the permission. We lose the ability
     * to verify the checksum ourselves — a real trade, made explicit here —
     * in exchange for not carrying the permission Play Protect reacts to.
     */
    fun openInBrowser(update: Update) {
        context.startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse(update.url))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    /**
     * Call at launch. If we are now running a version at or above what was
     * downloaded, the install succeeded: tell the server (it prunes older
     * APKs from R2) and clear every downloaded APK off this device.
     */
    fun onInstalled() {
        val pending = prefs.getInt("pending_version", -1)
        if (pending < 0) { clearDownloads(); return }
        if (BuildConfig.VERSION_CODE < pending) return   // not installed yet

        runCatching {
            val form = okhttp3.FormBody.Builder()
                .add("version_code", BuildConfig.VERSION_CODE.toString()).build()
            http.newCall(Request.Builder()
                .url("${BuildConfig.API_BASE}/v1/app/installed").post(form).build())
                .execute().close()
        }
        prefs.edit().remove("pending_version").apply()
        clearDownloads()
    }

    /** Old APKs are dead weight and a stale-binary risk — remove them all. */
    fun clearDownloads() {
        apkDir.listFiles()?.forEach { it.delete() }
    }

    private fun sha256Of(file: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { s ->
            val buf = ByteArray(64 * 1024)
            var read: Int
            while (s.read(buf).also { read = it } > 0) md.update(buf, 0, read)
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}
