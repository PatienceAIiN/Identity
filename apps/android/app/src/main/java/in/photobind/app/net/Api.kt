package `in`.photobind.app.net

import `in`.photobind.app.BuildConfig
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/** Thin API client. Session lives in an httpOnly cookie held in memory and
 *  mirrored into EncryptedSharedPreferences by SessionStore. */
class Api(private val sessionStore: SessionStore) {

    class ApiError(val code: Int, message: String) : Exception(message)

    /**
     * Holds the session cookie and mirrors it into encrypted storage, so being
     * signed in survives the app closing — and surviving an update is the same
     * thing, since an update is just a restart with the same storage.
     *
     * The old version replaced its whole cookie list with whatever a response
     * carried, so any response that set no cookies dropped the session for the
     * rest of that run, and an empty or expired pb_session could overwrite a
     * good stored one. Now the stored value only changes when the server sends a
     * real replacement, and only an explicit sign-out clears it.
     */
    private val jar = object : CookieJar {
        /** Every cookie the server sets, kept by name. The free-trial counter
         *  uses one of these too, so dropping the others would quietly hand
         *  someone a fresh trial on every request. */
        private val held = mutableMapOf<String, Cookie>()

        override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
            for (c in cookies) {
                val cleared = c.value.isBlank() ||
                    c.expiresAt < System.currentTimeMillis()
                if (cleared) held.remove(c.name) else held[c.name] = c
                if (c.name == "pb_session") {
                    // The server clearing this is a sign-out; anything else is a
                    // replacement worth keeping across restarts.
                    if (cleared) sessionStore.clear() else sessionStore.save(c.value)
                }
            }
        }

        override fun loadForRequest(url: HttpUrl): List<Cookie> {
            if (!held.containsKey("pb_session")) {
                sessionStore.load()?.let { stored ->
                    held["pb_session"] = Cookie.Builder().name("pb_session")
                        .value(stored).domain(url.host).path("/").build()
                }
            }
            return held.values.toList()
        }
    }

    private val client = OkHttpClient.Builder().cookieJar(jar).build()
    private val base = BuildConfig.API_BASE
    private val json = "application/json".toMediaType()

    /** Network failures reach the screen as plain language. OkHttp's own text
     *  ("Unable to resolve host …: No address associated with hostname") is
     *  accurate and useless to the person holding the phone. */
    private inline fun <T> withFriendlyErrors(block: () -> T): T = try {
        block()
    } catch (e: java.net.UnknownHostException) {
        throw ApiError(0, "No connection. Check your internet and try again.")
    } catch (e: java.net.SocketTimeoutException) {
        throw ApiError(0, "The server took too long to answer. Try again.")
    } catch (e: java.io.IOException) {
        throw ApiError(0, "Couldn't reach the server. Check your connection and try again.")
    }

    private fun run(req: Request): JSONObject = withFriendlyErrors {
        client.newCall(req).execute().use { res ->
            val body = res.body?.string().orEmpty()
            if (!res.isSuccessful) {
                val msg = runCatching { JSONObject(body).optString("detail") }
                    .getOrNull()?.takeIf { it.isNotBlank() } ?: "Request failed"
                throw ApiError(res.code, msg)
            }
            if (body.isBlank()) JSONObject() else JSONObject(body)
        }
    }

    fun post(path: String, payload: JSONObject): JSONObject =
        run(Request.Builder().url(base + path)
            .post(payload.toString().toRequestBody(json)).build())

    fun patch(path: String, payload: JSONObject): JSONObject =
        run(Request.Builder().url(base + path)
            .patch(payload.toString().toRequestBody(json)).build())

    fun put(path: String, payload: JSONObject): JSONObject =
        run(Request.Builder().url(base + path)
            .put(payload.toString().toRequestBody(json)).build())

    fun get(path: String): JSONObject =
        run(Request.Builder().url(base + path).get().build())

    /** Raw bytes, for a code's own picture. */
    fun getBytes(path: String): ByteArray = withFriendlyErrors {
        client.newCall(Request.Builder().url(base + path).get().build())
            .execute().use { res ->
                if (!res.isSuccessful) throw ApiError(res.code, "Couldn't load the picture")
                res.body?.bytes() ?: ByteArray(0)
            }
    }

    fun delete(path: String, payload: JSONObject? = null): JSONObject =
        run(Request.Builder().url(base + path).apply {
            if (payload != null) delete(payload.toString().toRequestBody(json))
            else delete()
        }.build())

    fun createCode(photo: ByteArray, ciphertextB64: String, nonceB64: String,
                   label: String, fragmentKey: String,
                   coverage: String = "full"): JSONObject {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("photo", "photo.jpg",
                photo.toRequestBody("image/jpeg".toMediaType()))
            .addFormDataPart("ciphertext_b64", ciphertextB64)
            .addFormDataPart("nonce_b64", nonceB64)
            .addFormDataPart("label", label)
            .addFormDataPart("encode_qr", "1")
            .addFormDataPart("fragment_key", fragmentKey)
            .addFormDataPart("coverage", coverage)
            .build()
        return run(Request.Builder().url("$base/v1/codes").post(body).build())
    }

    /** Guest mode: generates a code and returns it, saving nothing. The server
     *  counts these against the free-trial limit. */
    fun createTrialCode(photo: ByteArray, payload: String,
                        coverage: String = "full"): JSONObject {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("photo", "photo.jpg",
                photo.toRequestBody("image/jpeg".toMediaType()))
            .addFormDataPart("payload", payload)
            .addFormDataPart("coverage", coverage)
            .build()
        return run(Request.Builder().url("$base/v1/trial/codes").post(body).build())
    }

    fun signOut() {
        runCatching { post("/v1/auth/signout", JSONObject()) }
        sessionStore.clear()
    }
}
