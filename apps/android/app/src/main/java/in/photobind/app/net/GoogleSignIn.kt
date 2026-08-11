package `in`.photobind.app.net

import android.content.Context
import androidx.credentials.CredentialManager
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import com.google.android.libraries.identity.googleid.GetGoogleIdOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
import `in`.photobind.app.BuildConfig

/**
 * Google sign-in through Credential Manager — the current API; the legacy
 * Google Sign-In SDK is deprecated.
 *
 * The account picker returns an ID token minted for our *web* client id. The
 * app never inspects or trusts it: it is posted to the server, which verifies
 * signature, audience, issuer and expiry before creating a session. So a
 * tampered token gets a phone nothing.
 */
class GoogleSignIn(private val context: Context) {

    val configured: Boolean get() = BuildConfig.GOOGLE_WEB_CLIENT_ID.isNotBlank()

    sealed interface Result {
        data class Token(val idToken: String) : Result
        data object Cancelled : Result
        /** useBrowserFallback: Credential Manager cannot run here (no Google
         *  account, no Play services, or this build is not registered), but the
         *  web flow can still sign the person in. */
        data class Failed(val reason: String, val useBrowserFallback: Boolean = false) : Result
    }

    /** Last resort: the web sign-in page, which uses Google's browser flow and
     *  needs nothing registered against the app's signing key. */
    fun openBrowserSignIn() {
        val base = BuildConfig.API_BASE.trimEnd('/')
        context.startActivity(
            android.content.Intent(android.content.Intent.ACTION_VIEW,
                android.net.Uri.parse("$base/app/auth.html"))
                .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    /** Shows the account picker. Must be called from a coroutine. */
    suspend fun requestIdToken(filterByAuthorized: Boolean = false): Result {
        if (!configured) return Result.Failed("Google sign-in isn't set up in this build.")
        val option = GetGoogleIdOption.Builder()
            .setServerClientId(BuildConfig.GOOGLE_WEB_CLIENT_ID)
            // false = show every Google account on the device, not only ones
            // that have used this app before, so a first sign-in works.
            .setFilterByAuthorizedAccounts(filterByAuthorized)
            .setAutoSelectEnabled(false)
            .build()
        val request = GetCredentialRequest.Builder().addCredentialOption(option).build()
        return try {
            val response = CredentialManager.create(context)
                .getCredential(context, request)
            val cred = response.credential
            if (cred is CustomCredential &&
                cred.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL) {
                Result.Token(GoogleIdTokenCredential.createFrom(cred.data).idToken)
            } else {
                Result.Failed("Unexpected credential type from Google.")
            }
        } catch (e: androidx.credentials.exceptions.GetCredentialCancellationException) {
            Result.Cancelled
        } catch (e: androidx.credentials.exceptions.NoCredentialException) {
            // Either there is no Google account on the device, or this build is
            // not registered with Google (an Android OAuth client with the app's
            // signing SHA-1). Both are recoverable in a browser.
            Result.Failed(
                "No Google account available to this app on this device.",
                useBrowserFallback = true)
        } catch (e: androidx.credentials.exceptions.GetCredentialProviderConfigurationException) {
            Result.Failed("Google Play services isn't available here.",
                useBrowserFallback = true)
        } catch (e: Throwable) {
            Result.Failed(e.message ?: "Google sign-in failed. Try again.",
                useBrowserFallback = true)
        }
    }
}
