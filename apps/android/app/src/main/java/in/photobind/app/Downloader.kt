package `in`.photobind.app

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.io.File

/**
 * Saves a finished code into the phone's own picture library, so it can be
 * shared from wherever the person normally shares a photo.
 *
 * On Android 10 and later this goes through MediaStore and needs no permission.
 * Below that there is no MediaStore-owned Pictures collection to write into, so
 * it falls back to the public Pictures directory.
 */
object Downloader {

    /** Returns a short sentence to show the person, or null if it failed. */
    fun savePng(context: Context, bytes: ByteArray, name: String): String? {
        val filename = "identity-${name.take(40).replace(Regex("[^A-Za-z0-9_-]"), "")}.png"
        return runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.Images.Media.DISPLAY_NAME, filename)
                    put(MediaStore.Images.Media.MIME_TYPE, "image/png")
                    put(MediaStore.Images.Media.RELATIVE_PATH,
                        Environment.DIRECTORY_PICTURES + "/Identity")
                    put(MediaStore.Images.Media.IS_PENDING, 1)
                }
                val resolver = context.contentResolver
                val uri = resolver.insert(
                    MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
                    ?: return null
                resolver.openOutputStream(uri)?.use { it.write(bytes) } ?: return null
                values.clear()
                values.put(MediaStore.Images.Media.IS_PENDING, 0)
                resolver.update(uri, values, null, null)
                "Saved to Pictures / Identity"
            } else {
                val dir = File(
                    Environment.getExternalStoragePublicDirectory(
                        Environment.DIRECTORY_PICTURES), "Identity")
                dir.mkdirs()
                File(dir, filename).writeBytes(bytes)
                "Saved to Pictures/Identity"
            }
        }.getOrNull()
    }
}
