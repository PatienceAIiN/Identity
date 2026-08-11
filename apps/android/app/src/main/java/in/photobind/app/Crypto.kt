package `in`.photobind.app

import android.util.Base64
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * AES-256-GCM, mirroring the web client's WebCrypto usage exactly so a
 * payload encrypted on either platform decrypts on the other:
 *   - 256-bit key from SecureRandom
 *   - 96-bit nonce
 *   - 128-bit auth tag (Java appends it to the ciphertext, as WebCrypto does)
 *   - base64url without padding, matching the URL fragment format
 *
 * The key never leaves the device except inside the link fragment the user
 * chooses to share.
 */
object Crypto {
    private const val TAG_BITS = 128
    private const val NONCE_BYTES = 12
    private const val B64 = Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP

    data class Sealed(val ciphertextB64: String, val nonceB64: String, val keyB64: String)

    fun b64(bytes: ByteArray): String = Base64.encodeToString(bytes, B64)
    fun unb64(s: String): ByteArray = Base64.decode(s, B64)

    fun newKey(): ByteArray = ByteArray(32).also { SecureRandom().nextBytes(it) }

    fun seal(plaintext: String, key: ByteArray = newKey()): Sealed {
        val nonce = ByteArray(NONCE_BYTES).also { SecureRandom().nextBytes(it) }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"),
            GCMParameterSpec(TAG_BITS, nonce))
        val ct = cipher.doFinal(plaintext.toByteArray(Charsets.UTF_8))
        return Sealed(b64(ct), b64(nonce), b64(key))
    }

    fun open(ciphertextB64: String, nonceB64: String, keyB64: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(unb64(keyB64), "AES"),
            GCMParameterSpec(TAG_BITS, unb64(nonceB64)))
        return String(cipher.doFinal(unb64(ciphertextB64)), Charsets.UTF_8)
    }
}
