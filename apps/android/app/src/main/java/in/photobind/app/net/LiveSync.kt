package `in`.photobind.app.net

import `in`.photobind.app.BuildConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Live updates on the phone, from the same event stream the web app uses.
 *
 * Plain Server-Sent Events over OkHttp — one long-lived GET, no extra library,
 * works through Cloudflare. Events only name what changed; the caller refetches,
 * so a dropped event costs a stale view until the next one, never a wrong write.
 *
 * Reconnects with backoff so a server restart or a tunnel change heals without
 * the person doing anything.
 */
class LiveSync(private val sessionStore: SessionStore) {

    private val http = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)   // a stream must not time out
        .retryOnConnectionFailure(true)
        .build()

    /** Starts listening; cancel the returned job to stop. */
    fun start(scope: CoroutineScope, onEvent: (String, JSONObject) -> Unit) =
        scope.launch(Dispatchers.IO) {
            var backoff = 1000L
            while (isActive) {
                try {
                    val req = Request.Builder()
                        .url("${BuildConfig.API_BASE}/v1/events")
                        .header("Accept", "text/event-stream")
                        .apply {
                            sessionStore.load()?.let { header("Cookie", "pb_session=$it") }
                        }
                        .build()
                    http.newCall(req).execute().use { res ->
                        if (!res.isSuccessful) error("stream refused (${res.code})")
                        backoff = 1000L                       // a good connection resets it
                        val source = res.body?.source() ?: error("no body")
                        while (isActive && !source.exhausted()) {
                            val line = source.readUtf8LineStrict()
                            if (!line.startsWith("data: ")) continue   // skip comments/keepalives
                            val body = line.removePrefix("data: ")
                            if (body.isBlank() || body == "{}") continue
                            val json = runCatching { JSONObject(body) }.getOrNull() ?: continue
                            onEvent(json.optString("event"), json)
                        }
                    }
                } catch (_: Throwable) {
                    // Any failure: wait, then try again. Never crash the app for
                    // a background stream.
                }
                if (!isActive) break
                delay(backoff)
                backoff = (backoff * 2).coerceAtMost(30_000)
            }
        }
}
