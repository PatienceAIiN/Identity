package `in`.photobind.app.net

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** Session token at rest in EncryptedSharedPreferences (CLAUDE.md §6).
 *  Never the payload key — that only exists in the share link. */
class SessionStore(context: Context) {
    private val prefs = EncryptedSharedPreferences.create(
        context,
        "identity_session",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    fun save(token: String) = prefs.edit().putString("token", token).apply()
    fun load(): String? = prefs.getString("token", null)
    fun clear() = prefs.edit().remove("token").apply()
}
