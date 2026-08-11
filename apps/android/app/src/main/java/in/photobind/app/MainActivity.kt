package `in`.photobind.app

import android.app.Activity
import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.core.view.WindowInsetsControllerCompat
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AddAPhoto
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DarkMode
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Login
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.QrCode2
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.layout.LastBaseline
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import `in`.photobind.app.net.Api
import `in`.photobind.app.net.GoogleSignIn
import `in`.photobind.app.net.LiveSync
import `in`.photobind.app.net.SessionStore
import `in`.photobind.app.ota.UpdateManager
import `in`.photobind.app.ota.UpdateSheet
import `in`.photobind.app.report.CrashReportedNotice
import `in`.photobind.app.report.FeedbackSheet
import `in`.photobind.app.report.Reporter
import `in`.photobind.app.scan.CameraScanner
import `in`.photobind.app.ui.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** Photo the user picked, held as Compose state so screens observe it directly. */
class PhotoPicker(private val activity: ComponentActivity) {
    var bytes by mutableStateOf<ByteArray?>(null)
        private set
    var displayName by mutableStateOf("")
        private set

    private val launcher = activity.registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            result.data?.data?.let { uri: Uri ->
                bytes = activity.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                displayName = uri.lastPathSegment?.takeLast(28) ?: "photo"
            }
        }
    }

    fun pick() = launcher.launch(Intent(Intent.ACTION_GET_CONTENT).apply { type = "image/*" })
    fun clear() { bytes = null; displayName = "" }
}

class MainActivity : ComponentActivity() {

    private lateinit var api: Api
    private lateinit var photoPicker: PhotoPicker

    override fun onCreate(savedInstanceState: Bundle?) {
        // Keep the animated splash on screen until the first real frame is ready,
        // so the mark hands over to the app instead of flashing a blank window.
        val splash = installSplashScreen()
        super.onCreate(savedInstanceState)
        var ready = false
        splash.setKeepOnScreenCondition { !ready }

        api = Api(SessionStore(applicationContext))
        photoPicker = PhotoPicker(this)   // must register before RESUMED
        Reporter(applicationContext).installCrashHandler()

        val theme = ThemeState(applicationContext)
        setContent {
            IdentityTheme(theme) {
                // The status and navigation bars follow the app's own theme, not
                // the system's. Someone running a dark phone who chooses light
                // here gets light chrome to match, and the icons in those bars
                // stay readable against it either way.
                val bg = AppColors.bg
                val dark = theme.isDark
                LaunchedEffect(dark) {
                    window.statusBarColor = bg.toArgb()
                    window.navigationBarColor = bg.toArgb()
                    WindowInsetsControllerCompat(window, window.decorView).apply {
                        isAppearanceLightStatusBars = !dark
                        isAppearanceLightNavigationBars = !dark
                    }
                }
                AppRoot(api, photoPicker) { ready = true }
            }
        }
    }
}

private enum class Screen(val label: String, val icon: ImageVector) {
    Auth("Sign in", Icons.Filled.Login),
    Home("Home", Icons.Filled.Home),
    New("New code", Icons.Filled.AddAPhoto),
    Codes("Codes", Icons.Filled.QrCode2),
    Usage("Usage", Icons.Filled.BarChart),
    Scan("Scan", Icons.Filled.QrCodeScanner),
    Profile("Profile", Icons.Filled.Person),
}

private val TABS = listOf(Screen.Home, Screen.New, Screen.Scan, Screen.Codes,
                          Screen.Usage, Screen.Profile)

@Composable
private fun AppRoot(api: Api, picker: PhotoPicker, onReady: () -> Unit) {
    var screen by remember { mutableStateOf(Screen.Auth) }
    var me by remember { mutableStateOf<JSONObject?>(null) }
    // Guest mode: the free trial, in the app. Nothing is stored server-side and
    // there is no session, so every signed-in destination stays out of reach.
    var guest by remember { mutableStateOf(false) }
    // Where back should go. Home is the floor; from there we ask to leave.
    val history = remember { mutableStateListOf<Screen>() }
    var confirmExit by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current

    fun go(next: Screen) {
        if (next == screen) return
        if (screen != Screen.Auth) history.add(screen)
        screen = next
    }

    // Hand the system splash over as soon as the app can draw its own first
    // frame. This used to wait for the session check to come back from the
    // network, which meant a slow connection held a frozen logo on screen for as
    // long as the request took — and an unreachable server held it until the
    // socket timed out.
    var booting by remember { mutableStateOf(true) }
    LaunchedEffect(Unit) { onReady() }
    LaunchedEffect(Unit) {
        runCatching { withContext(Dispatchers.IO) { api.get("/v1/me") } }
            .onSuccess { me = it; screen = Screen.Home }
        booting = false
    }

    val reporter = remember { Reporter(ctx) }
    var crashSummary by remember { mutableStateOf<String?>(null) }
    var feedbackKind by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        withContext(Dispatchers.IO) {
            if (reporter.hasPendingCrash()) crashSummary = reporter.flushPendingCrash()
        }
    }

    val updater = remember { UpdateManager(ctx) }
    var updateStatus by remember {
        mutableStateOf<UpdateManager.Status>(UpdateManager.Status.UpToDate) }
    LaunchedEffect(Unit) {
        withContext(Dispatchers.IO) {
            updater.onInstalled()
            updateStatus = updater.check()
        }
    }

    var codesVersion by remember { mutableStateOf(0) }
    LaunchedEffect(Unit) {
        val syncScope = this
        LiveSync(SessionStore(ctx)).start(syncScope) { event, _ ->
            when (event) {
                "codes.changed", "scan.recorded" -> codesVersion++
                "release.published" -> syncScope.launch(Dispatchers.IO) {
                    updateStatus = updater.check()
                }
                "session.ended" -> { me = null; history.clear(); screen = Screen.Auth }
            }
        }
    }

    // Back never drops out of the app from an inner screen: it walks the history
    // back to Home, and only there does it ask whether to leave.
    BackHandler(enabled = true) {
        when {
            screen == Screen.Auth -> confirmExit = true
            history.isNotEmpty() -> screen = history.removeAt(history.lastIndex)
            guest && screen != Screen.New -> screen = Screen.New
            guest -> confirmExit = true
            screen != Screen.Home -> screen = Screen.Home
            else -> confirmExit = true
        }
    }

    crashSummary?.let { summary ->
        CrashReportedNotice(summary,
            onDetails = { crashSummary = null; feedbackKind = "bug" },
            onDismiss = { crashSummary = null })
    }
    feedbackKind?.let { k ->
        FeedbackSheet(initialKind = k,
            prefill = if (k == "bug") "The app closed unexpectedly" else "") {
            feedbackKind = null
        }
    }
    if (updateStatus is UpdateManager.Status.Available ||
        updateStatus is UpdateManager.Status.Unsupported) {
        UpdateSheet(updateStatus) { updateStatus = UpdateManager.Status.UpToDate }
    }
    if (confirmExit) {
        val activity = ctx as? Activity
        ConfirmDialog(
            title = "Leave Identity?",
            body = "Your codes keep working whether the app is open or not.",
            confirmLabel = "Leave", cancelLabel = "Stay",
            onConfirm = { confirmExit = false; activity?.finish() },
            onDismiss = { confirmExit = false })
    }

    Scaffold(
        containerColor = AppColors.bg,
        // Explicit, so any Text without a colour of its own inherits ink rather
        // than whatever contentColorFor() resolves to. Text the same colour as
        // the page it sits on is the failure this prevents.
        contentColor = AppColors.ink,
        topBar = { if (screen != Screen.Auth) TopBar(screen) },
        bottomBar = {
            if (screen != Screen.Auth) {
                // A guest has no codes, no dashboard and no profile — offering
                // them would be four taps to a sign-in wall.
                val tabs = if (guest) listOf(Screen.New, Screen.Scan) else TABS
                NavBar(tabs, screen) { go(it) }
            }
        },
    ) { pad ->
        Box(Modifier.padding(pad)) {
            if (booting) {
                // The app's own waiting state, in the app's own colours, rather
                // than the system splash frozen over a network call.
                Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center) {
                    CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                        color = Accent)
                    Spacer(Modifier.height(SM))
                    Text("Identity", style = MaterialTheme.typography.titleLarge,
                        color = AppColors.muted)
                }
                return@Box
            }
            when (screen) {
                Screen.Auth -> AuthScreen(api,
                    onGuest = { guest = true; history.clear(); screen = Screen.New },
                    onSignedIn = { user ->
                        guest = false; me = user; history.clear()
                        screen = Screen.Home
                    })
                Screen.Home -> DashboardScreen(api, me, codesVersion) { go(it) }
                Screen.New -> NewCodeScreen(api, picker, guest = guest,
                    onWantAccount = { guest = false; history.clear(); screen = Screen.Auth })
                Screen.Scan -> ScanScreen(api)
                Screen.Codes -> CodesScreen(api, codesVersion)
                Screen.Usage -> UsageScreen(api, codesVersion)
                Screen.Profile -> ProfileScreen(api, me) {
                    me = null; history.clear(); screen = Screen.Auth
                }
            }
        }
    }
}

@Composable
private fun TopBar(screen: Screen) {
    Column {
        Row(Modifier.fillMaxWidth().padding(horizontal = MD, vertical = SM),
            verticalAlignment = Alignment.CenterVertically) {
            // The dot sits on the wordmark's own baseline via alignBy, so it
            // stays put whatever the text scale — it used to be nudged with a
            // fixed padding, which drifts as soon as font size changes.
            Row(modifier = Modifier.weight(1f)) {
                Text("Identity", style = MaterialTheme.typography.headlineMedium,
                    color = AppColors.ink,
                    modifier = Modifier.alignBy(LastBaseline))
                Spacer(Modifier.width(XS))
                Box(Modifier.alignBy { it.measuredHeight }.size(7.dp)
                    .background(Live))
            }
            // The theme switch lives on Profile, with the rest of the settings.
        }
        Rule()
    }
}

@Composable
private fun NavBar(tabs: List<Screen>, current: Screen, go: (Screen) -> Unit) {
    Column {
        Rule()
        NavigationBar(containerColor = AppColors.bg, tonalElevation = 0.dp) {
            tabs.forEach { s ->
                NavigationBarItem(
                    selected = current == s,
                    onClick = { go(s) },
                    icon = {
                        Icon(s.icon, contentDescription = null,
                             modifier = Modifier.size(22.dp))
                    },
                    label = {
                        Text(s.label, fontFamily = Mono, fontSize = 10.sp,
                            fontWeight = if (current == s) FontWeight.Bold
                                         else FontWeight.Normal)
                    },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = Accent,
                        unselectedIconColor = AppColors.muted,
                        selectedTextColor = Accent,
                        unselectedTextColor = AppColors.muted,
                        indicatorColor = Color.Transparent),
                )
            }
        }
    }
}

/* ── auth ─────────────────────────────────────────────────────────────────── */

@Composable
private fun AuthScreen(api: Api, onGuest: () -> Unit,
                       onSignedIn: (JSONObject) -> Unit) {
    var step by remember { mutableStateOf("form") }        // form | verify
    var signUp by remember { mutableStateOf(false) }
    var name by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var pass by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var acceptTerms by remember { mutableStateOf(false) }
    var legal by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf("") }
    var notice by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var googleBusy by remember { mutableStateOf(false) }
    var showGuestInfo by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    val google = remember { GoogleSignIn(ctx) }

    Column(Modifier.fillMaxSize().padding(horizontal = MD)
        .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(SM)) {

        Spacer(Modifier.height(XL))
        BrandMark()
        Spacer(Modifier.height(SM))

        if (step == "verify") {
            Text("Check your email", style = MaterialTheme.typography.headlineMedium,
                color = AppColors.ink)
            Text("We sent a 6-digit code to $email. It expires in 15 minutes.",
                style = MaterialTheme.typography.bodyMedium, color = AppColors.ink)
            Spacer(Modifier.height(XS))
            Field("Verification code", code,
                { code = it.filter(Char::isDigit).take(6) },
                placeholder = "000000", numeric = true)
            if (notice.isNotEmpty()) Text(notice,
                style = MaterialTheme.typography.labelSmall, color = AppColors.muted)
            if (error.isNotEmpty()) ErrorNote(error)
            PrimaryButton("Verify and create account", Modifier.fillMaxWidth(),
                loading = busy, enabled = code.length == 6) {
                error = ""; busy = true
                scope.launch {
                    runCatching {
                        withContext(Dispatchers.IO) {
                            api.post("/v1/auth/verify-email",
                                JSONObject().put("email", email).put("code", code))
                        }
                    }.onSuccess { onSignedIn(it) }
                     .onFailure { error = it.message ?: "That code didn't work." }
                    busy = false
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(SM)) {
                SecondaryButton("New code", Modifier.weight(1f), enabled = !busy) {
                    error = ""
                    scope.launch {
                        runCatching {
                            withContext(Dispatchers.IO) {
                                api.post("/v1/auth/resend-code",
                                    JSONObject().put("email", email))
                            }
                        }.onSuccess { notice = "A new code is on its way." }
                         .onFailure { error = it.message ?: "Couldn't send another code." }
                    }
                }
                SecondaryButton("Back", Modifier.weight(1f), enabled = !busy) {
                    step = "form"; error = ""; notice = ""; code = ""
                }
            }
            Spacer(Modifier.height(XL))
            legal?.let { LegalSheet(it) { legal = null } }
            return@Column
        }

        // One switch, so which of the two things you're doing is visible at a
        // glance instead of inferred from a button at the bottom.
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(SM)) {
            if (!signUp) PrimaryButton("Sign in", Modifier.weight(1f)) {}
            else SecondaryButton("Sign in", Modifier.weight(1f)) {
                signUp = false; error = ""
            }
            if (signUp) PrimaryButton("Sign up", Modifier.weight(1f)) {}
            else SecondaryButton("Sign up", Modifier.weight(1f)) {
                signUp = true; error = ""
            }
        }
        Spacer(Modifier.height(XS))

        if (signUp) Field("Name", name, { name = it }, placeholder = "Ama Osei")
        Field("Email", email, { email = it }, placeholder = "you@clinic.org")
        Field("Password", pass, { pass = it },
            placeholder = if (signUp) "at least 12 characters" else "your password",
            password = true)

        if (signUp) {
            Spacer(Modifier.height(XS))
            ConsentRow(acceptTerms, { acceptTerms = it; if (it) error = "" },
                { legal = "terms" }, { legal = "privacy" })
        }

        if (error.isNotEmpty()) ErrorNote(error)

        PrimaryButton(if (signUp) "Create account" else "Sign in",
            Modifier.fillMaxWidth(), loading = busy,
            enabled = !busy && (!signUp || acceptTerms)) {
            when {
                !email.contains("@") ->
                    error = "That email address is missing an @."
                signUp && name.isBlank() -> error = "Add the name for your codes."
                signUp && pass.length < 12 ->
                    error = "Passwords need at least 12 characters."
                else -> {
                    error = ""; busy = true
                    scope.launch {
                        val body = JSONObject().put("email", email).put("password", pass)
                        // The real checkbox value, never a hardcoded true: the
                        // button gate is a convenience, the server's consent
                        // check is the thing that decides.
                        if (signUp) body.put("name", name).put("accept_terms", acceptTerms)
                        val path = if (signUp) "/v1/auth/signup" else "/v1/auth/signin"
                        runCatching {
                            withContext(Dispatchers.IO) { api.post(path, body) }
                        }.onSuccess { res ->
                            if (signUp) {
                                step = "verify"
                                notice = if (res.optString("delivery") in
                                    listOf("brevo-api", "smtp")) ""
                                    else "email isn't configured on the server, so the " +
                                         "code went to its log"
                            } else onSignedIn(res)
                        }.onFailure { e ->
                            val msg = e.message ?: "Something went wrong."
                            when {
                                msg.contains("No account", true) -> {
                                    signUp = true
                                    error = "No account for that email yet. Create one."
                                }
                                msg.contains("isn't finished", true) -> step = "verify"
                                else -> error = msg
                            }
                        }
                        busy = false
                    }
                }
            }
        }



        if (google.configured) {
            Row(Modifier.fillMaxWidth().padding(vertical = XS),
                verticalAlignment = Alignment.CenterVertically) {
                HorizontalDivider(Modifier.weight(1f), 1.dp, AppColors.line)
                Text("or", style = MaterialTheme.typography.labelSmall,
                    color = AppColors.muted, modifier = Modifier.padding(horizontal = SM))
                HorizontalDivider(Modifier.weight(1f), 1.dp, AppColors.line)
            }
            SecondaryButton("Continue with Google", Modifier.fillMaxWidth(),
                loading = googleBusy, enabled = !googleBusy) {
                error = ""; googleBusy = true
                scope.launch {
                    when (val r = google.requestIdToken()) {
                        is GoogleSignIn.Result.Token -> {
                            // Consent is sent as ticked or not — never assumed.
                            // Signing in must not be able to create an account
                            // nobody agreed to terms for; the server refuses with
                            // 422 and we then ask for it explicitly.
                            val body = JSONObject()
                                .put("id_token", r.idToken)
                                .put("accept_terms", acceptTerms)
                            runCatching {
                                withContext(Dispatchers.IO) {
                                    api.post("/v1/auth/google", body)
                                }
                            }.onSuccess { onSignedIn(it) }
                             .onFailure { e ->
                                 val msg = e.message ?: "Google sign-in failed."
                                 if (msg.contains("terms", true)) {
                                     signUp = true
                                     error = "First time with this Google account — " +
                                             "tick the box to agree, then try again."
                                 } else error = msg
                             }
                        }
                        is GoogleSignIn.Result.Cancelled -> {}
                        is GoogleSignIn.Result.Failed -> {
                            error = r.reason
                            if (r.useBrowserFallback) {
                                google.openBrowserSignIn()
                                error = "Opening sign-in in your browser instead."
                            }
                        }
                    }
                    googleBusy = false
                }
            }
        }

        // Guest comes last, after the two ways of having an account. What the
        // trial actually costs you is explained in the sheet it opens, rather
        // than as a wall of small print under the button.
        Rule()
        SecondaryButton("Continue as a guest", Modifier.fillMaxWidth(),
            enabled = !busy && !googleBusy) { showGuestInfo = true }

        Spacer(Modifier.height(LG))
        Text("A product of Patience AI", fontFamily = Mono, fontSize = 11.sp,
            color = AppColors.muted,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
            modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(XL))
    }
    legal?.let { LegalSheet(it) { legal = null } }
    if (showGuestInfo) {
        GuestSheet(
            onContinue = { showGuestInfo = false; onGuest() },
            onDismiss = { showGuestInfo = false })
    }
}

/** What guest mode is, said once, where it is asked for. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun GuestSheet(onContinue: () -> Unit, onDismiss: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet,
        containerColor = AppColors.bg, shape = RectangleShape,
        dragHandle = { Box(Modifier.fillMaxWidth().padding(top = SM),
            contentAlignment = Alignment.Center) {
            Box(Modifier.size(width = 42.dp, height = 4.dp).background(AppColors.line))
        } }) {
        Column(Modifier.fillMaxWidth().padding(MD),
            verticalArrangement = Arrangement.spacedBy(SM)) {
            Kicker("guest mode", Blue)
            Text("Five codes, no account", style = MaterialTheme.typography.headlineMedium,
                color = AppColors.ink)
            Text("Make up to five codes without signing up. They work like any "
                 + "other code — any camera reads them.",
                style = MaterialTheme.typography.bodyMedium, color = AppColors.ink)
            Rule()
            Text("What you don't get", style = MaterialTheme.typography.titleLarge,
                color = AppColors.ink)
            Text("Guest codes aren't saved, so they can't be listed, switched off "
                 + "after you've shared one, or traced to a copy. The link sits "
                 + "inside the picture itself, which means anyone who scans it "
                 + "can read it, and there is no way to take that back.",
                style = MaterialTheme.typography.bodyMedium, color = AppColors.muted)
            Spacer(Modifier.height(XS))
            PrimaryButton("Continue as a guest", Modifier.fillMaxWidth()) { onContinue() }
            SecondaryButton("Go back", Modifier.fillMaxWidth()) { onDismiss() }
            Spacer(Modifier.height(SM))
        }
    }
}

/* ── dashboard ────────────────────────────────────────────────────────────── */

@Composable
private fun DashboardScreen(api: Api, me: JSONObject?, liveVersion: Int,
                            go: (Screen) -> Unit) {
    var codes by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    LaunchedEffect(liveVersion) {
        runCatching { withContext(Dispatchers.IO) { api.get("/v1/codes") } }
            .onSuccess { r ->
                val a = r.getJSONArray("codes")
                codes = (0 until a.length()).map { a.getJSONObject(it) }
            }
        loading = false
    }

    val live = codes.count { it.optString("state") == "active" }
    val off = codes.count { it.optString("state") != "active" }
    val scans = codes.sumOf { it.optInt("scan_count") }

    Column(Modifier.fillMaxSize().padding(horizontal = MD)
        .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(SM)) {
        Spacer(Modifier.height(SM))
        Text(me?.optString("name")?.takeIf { it.isNotBlank() }
            ?.let { "Hello, ${it.substringBefore(' ')}" } ?: "Your dashboard",
            style = MaterialTheme.typography.headlineLarge, color = AppColors.ink)
        // The address is on Profile. A dashboard someone holds up, or hands over,
        // has no reason to carry it.
        SectionTitle("your codes at a glance")

        Spacer(Modifier.height(XS))
        Row(horizontalArrangement = Arrangement.spacedBy(SM)) {
            StatTile(if (loading) "—" else "$live", "live", Live,
                Modifier.weight(1f)) { go(Screen.Codes) }
            StatTile(if (loading) "—" else "$off", "switched off", Revoked,
                Modifier.weight(1f)) { go(Screen.Codes) }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(SM)) {
            StatTile(if (loading) "—" else "$scans", "scans", Blue,
                Modifier.weight(1f)) { go(Screen.Codes) }
            StatTile(if (loading) "—" else "${codes.size}", "codes", Accent,
                Modifier.weight(1f)) { go(Screen.Codes) }
        }

        Spacer(Modifier.height(SM))
        SectionTitle("do something")
        PrimaryButton("Create a new code", Modifier.fillMaxWidth()) { go(Screen.New) }
        SecondaryButton("Scan a code with the camera", Modifier.fillMaxWidth()) {
            go(Screen.Scan)
        }

        if (codes.isNotEmpty()) {
            Spacer(Modifier.height(SM))
            SectionTitle("most recent")
            codes.take(3).forEach { c -> CodeRow(c, compact = true) }
            SecondaryButton("See all codes", Modifier.fillMaxWidth()) { go(Screen.Codes) }
        } else if (!loading) {
            Spacer(Modifier.height(SM))
            Text("No codes yet. Make one and it will show up here with its scan history.",
                style = MaterialTheme.typography.bodyMedium, color = AppColors.muted)
        }
        Spacer(Modifier.height(XL))
    }
}

@Composable
private fun CodeRow(c: JSONObject, compact: Boolean = false,
                    onRevoke: (() -> Unit)? = null,
                    onOpen: (() -> Unit)? = null) {
    val state = c.optString("state")
    Column(Modifier.fillMaxWidth().background(AppColors.surface)
        .then(if (onOpen != null) Modifier.clickable { onOpen() } else Modifier)
        .padding(MD),
        verticalArrangement = Arrangement.spacedBy(XS)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(c.optString("opaque_resolution_id"), fontFamily = Mono,
                fontWeight = FontWeight.Bold, fontSize = 13.sp,
                color = AppColors.ink, modifier = Modifier.weight(1f))
            StateTag(state)
        }
        Text("${c.optString("label")} · ${c.optInt("scan_count")} scans · " +
             "decode ${c.optInt("decode_rate")}%",
            style = MaterialTheme.typography.labelSmall, color = AppColors.muted)
        if (!compact) {
            val log = c.optJSONArray("log")
            if (log != null && log.length() > 0) {
                Rule()
                (0 until log.length()).forEach { i ->
                    Text(log.getString(i), style = MaterialTheme.typography.labelSmall,
                        color = AppColors.muted)
                }
            }
            if (state == "active" && onRevoke != null) {
                SecondaryButton("Switch this copy off", Modifier.fillMaxWidth(),
                    color = Revoked) { onRevoke() }
            }
        }
    }
}

/* ── scan ─────────────────────────────────────────────────────────────────── */

@Composable
private fun ScanScreen(api: Api) {
    var decoded by remember { mutableStateOf<String?>(null) }
    var status by remember { mutableStateOf<String?>(null) }
    var detail by remember { mutableStateOf("") }
    var opens by remember { mutableStateOf("") }
    var checking by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current

    fun reset() {
        decoded = null; status = null; detail = ""; opens = ""; checking = false
    }

    Column(Modifier.fillMaxSize().padding(horizontal = MD)
        .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(SM)) {
        Spacer(Modifier.height(SM))
        Text("Scan a code", style = MaterialTheme.typography.headlineMedium,
            color = AppColors.ink)
        Text("Point the camera at a photo-bound code. Reading happens on this " +
             "phone — the picture is not uploaded.",
            style = MaterialTheme.typography.bodyMedium, color = AppColors.muted)

        Box(Modifier.fillMaxWidth().height(320.dp)) {
            CameraScanner(Modifier.fillMaxSize(), paused = decoded != null) { text ->
                decoded = text
                // If it is one of ours, ask the server what state it is in. That
                // is the difference between "a QR" and "a code you issued".
                val id = Regex("/r/([A-Za-z0-9_-]{6,})").find(text)?.groupValues?.get(1)
                if (id == null) {
                    status = "OTHER"
                } else {
                    checking = true
                    scope.launch {
                        runCatching {
                            withContext(Dispatchers.IO) { api.get("/r/$id") }
                        }.onSuccess { r ->
                            status = r.optString("status", "ACTIVE")
                            // What the code opens is encrypted, and the key rode
                            // along in the scanned link's fragment — so this
                            // phone can read it without the server ever being
                            // able to. No key, no plaintext: that is the whole
                            // point, and the screen shows the difference.
                            val key = text.substringAfter('#', "")
                            val ct = r.optString("ciphertext")
                            val nonce = r.optString("nonce")
                            if (key.isNotBlank() && ct.isNotBlank()) {
                                opens = runCatching { Crypto.open(ct, nonce, key) }
                                    .getOrElse { "" }
                            }
                        }.onFailure { e ->
                            val m = e.message ?: ""
                            status = when {
                                m.contains("410") || m.contains("REVOKED", true) -> "REVOKED"
                                m.contains("unknown", true) -> "UNKNOWN"
                                else -> "ERROR"
                            }
                            detail = m
                        }
                        checking = false
                    }
                }
            }
            // Framing corners, so it is obvious where to aim.
            listOf(Alignment.TopStart, Alignment.TopEnd, Alignment.BottomStart)
                .forEach { a ->
                    Box(Modifier.align(a).padding(SM).size(26.dp)
                        .background(Accent.copy(alpha = .18f)))
                }
        }

        if (checking) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp,
                    color = Accent)
                Spacer(Modifier.width(SM))
                Text("Checking this code…", style = MaterialTheme.typography.labelSmall,
                    color = AppColors.muted)
            }
        }

        decoded?.let { text ->
            val (bar, title, body) = when (status) {
                "ACTIVE" -> Triple(Live, "This code is live",
                    "It resolves right now. The owner can switch it off at any time.")
                "REVOKED" -> Triple(Revoked, "This code was switched off",
                    "The owner turned it off. Nothing was decrypted.")
                "EXPIRED" -> Triple(Expiring, "This code expired",
                    "It was set to stop working, and it has.")
                "UNKNOWN" -> Triple(Revoked, "No such code",
                    "This link doesn't match any code we know about.")
                "OTHER" -> Triple(Blue, "Not an Identity code",
                    "It scanned fine, but it isn't one of ours.")
                else -> Triple(AppColors.muted, "Couldn't check it",
                    detail.ifBlank { "The server didn't answer." })
            }
            Column(Modifier.fillMaxWidth().background(AppColors.surface)) {
                Box(Modifier.fillMaxWidth().height(4.dp).background(bar))
                Column(Modifier.padding(MD),
                    verticalArrangement = Arrangement.spacedBy(XS)) {
                    Text(title, style = MaterialTheme.typography.titleLarge,
                        color = AppColors.ink)
                    Text(body, style = MaterialTheme.typography.bodyMedium,
                        color = AppColors.ink)
                    if (opens.isNotBlank()) {
                        Spacer(Modifier.height(XS))
                        SectionTitle("it opens")
                        Text(opens, style = MaterialTheme.typography.bodyLarge,
                            color = AppColors.ink)
                    } else if (status == "ACTIVE") {
                        Text("This code is live, but the scanned link carried no "
                             + "key, so there is nothing here to read.",
                            style = MaterialTheme.typography.labelSmall,
                            color = AppColors.muted)
                    }
                    // The code's own link, without the key that followed the #.
                    Text(text.substringBefore('#'),
                        style = MaterialTheme.typography.labelSmall,
                        color = AppColors.muted)
                }
            }
            if (opens.startsWith("http://") || opens.startsWith("https://")) {
                PrimaryButton("Open the link", Modifier.fillMaxWidth()) {
                    runCatching {
                        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(opens)))
                    }
                }
            }
            SecondaryButton("Scan another", Modifier.fillMaxWidth()) { reset() }
        }
        Spacer(Modifier.height(XL))
    }
}

/* ── new code ─────────────────────────────────────────────────────────────── */

@Composable
private fun NewCodeScreen(api: Api, picker: PhotoPicker, guest: Boolean = false,
                          onWantAccount: () -> Unit = {}) {
    // Empty, not a plausible-looking example: a prefilled address is the one a
    // hurried person ships by accident.
    var payload by remember { mutableStateOf("") }
    var label by remember { mutableStateOf("") }
    var coverage by remember { mutableStateOf("full") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<JSONObject?>(null) }
    var link by remember { mutableStateOf("") }
    var saveNote by remember { mutableStateOf("") }
    var autoBusy by remember { mutableStateOf(false) }
    var trialLeft by remember { mutableStateOf<Int?>(null) }
    var quotaSpent by remember { mutableStateOf(false) }
    LaunchedEffect(guest, result) {
        if (guest) {
            runCatching { withContext(Dispatchers.IO) { api.get("/v1/trial/status") } }
                .onSuccess { trialLeft = it.optInt("remaining", 0) }
        }
    }
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()

    Column(Modifier.fillMaxSize().padding(horizontal = MD)
        .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(SM)) {
        Spacer(Modifier.height(SM))
        Text("New code", style = MaterialTheme.typography.headlineMedium,
            color = AppColors.ink)
        if (guest) {
            Column(Modifier.fillMaxWidth().background(AppColors.surface).padding(MD),
                verticalArrangement = Arrangement.spacedBy(XS)) {
                Kicker("guest · ${trialLeft ?: 5} of 5 left", Blue)
                Text("Guest codes aren't saved. Your link sits inside the "
                     + "picture, so anyone who scans it reads it — and it can't "
                     + "be switched off later.",
                    style = MaterialTheme.typography.bodyMedium, color = AppColors.ink)
                SecondaryButton("Create an account", Modifier.fillMaxWidth()) {
                    onWantAccount()
                }
            }
        }

        Field("What a scan opens *", payload, { payload = it },
            placeholder = "https://your-link.example")
        // Nothing to paste? Point it at your own card page. One tap, and it is
        // still an ordinary editable link afterwards.
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(SM)) {
            SecondaryButton("Use my card link", Modifier.weight(1f),
                loading = autoBusy) {
                autoBusy = true
                scope.launch {
                    runCatching {
                        withContext(Dispatchers.IO) { api.get("/v1/me/card") }
                    }.onSuccess { payload = it.optString("url") }
                     .onFailure { error = it.message ?: "Couldn't build a link." }
                    autoBusy = false
                }
            }
        }
        if (!guest) Field("Label for this copy", label, { label = it },
            placeholder = "LinkedIn")

        SectionTitle("coverage")
        Row(horizontalArrangement = Arrangement.spacedBy(SM)) {
            listOf("full" to "Whole picture", "auto" to "Keep face clear")
                .forEach { (v, text) ->
                    if (coverage == v)
                        PrimaryButton(text, Modifier.weight(1f)) { coverage = v }
                    else SecondaryButton(text, Modifier.weight(1f)) { coverage = v }
                }
        }

        SectionTitle("photo")
        // One bar: tap it to choose or change the picture, and clear it from the
        // same bar rather than hunting for the control somewhere else.
        Box(Modifier.fillMaxWidth()) {
            SecondaryButton(
                if (picker.bytes == null) "Choose a photo"
                else picker.displayName.ifBlank { "Photo selected" },
                Modifier.fillMaxWidth()) { picker.pick() }
            if (picker.bytes != null) {
                IconButton(
                    onClick = { picker.clear(); result = null; link = "" },
                    modifier = Modifier.align(Alignment.CenterEnd)
                        .padding(end = XS).size(40.dp)) {
                    Icon(Icons.Filled.Close, contentDescription = "Remove this photo",
                        tint = AppColors.ink, modifier = Modifier.size(20.dp))
                }
            }
        }

        if (error.isNotEmpty()) ErrorNote(error)

        PrimaryButton("Generate", Modifier.fillMaxWidth(), loading = busy) {
            val bytes = picker.bytes
            if (payload.isBlank()) {
                // Encrypting an empty string yields a code that resolves to
                // nothing — refusing here is better than finding out at a scan.
                error = "Type what a scan should open, or use your card link."
            } else if (bytes == null) error = "Choose a photo first."
            else {
                error = ""; busy = true
                scope.launch {
                    runCatching {
                        if (guest) {
                            // Guest codes carry the link inside the picture and
                            // are never stored, so there is nothing to encrypt
                            // against a credential that doesn't exist.
                            val r = withContext(Dispatchers.IO) {
                                api.createTrialCode(bytes, payload, coverage)
                            }
                            link = payload
                            r
                        } else {
                            val sealed = Crypto.seal(payload)
                            val r = withContext(Dispatchers.IO) {
                                api.createCode(bytes, sealed.ciphertextB64,
                                    sealed.nonceB64, label, sealed.keyB64, coverage)
                            }
                            link = r.getString("resolution_url") + "#" + sealed.keyB64
                            r
                        }
                    }.onSuccess { result = it }
                     .onFailure { e ->
                        // 402 is the trial running out — that is a moment to
                        // explain, not an error to report.
                        if (e is Api.ApiError && e.code == 402) quotaSpent = true
                        else error = e.message ?: "Couldn't make a code."
                     }
                    busy = false
                }
            }
        }

        result?.let { r ->
            Rule()
            SectionTitle("decode confidence")
            Text("${r.optInt("decode_rate")}%", fontFamily = Mono,
                fontWeight = FontWeight.Bold, fontSize = 34.sp, color = AppColors.ink)
            Text("Checked with a real scanner before we showed it to you.",
                style = MaterialTheme.typography.bodyMedium, color = AppColors.muted)
            val png = android.util.Base64.decode(
                r.getString("image_png_b64"), android.util.Base64.DEFAULT)
            runCatching { BitmapFactory.decodeByteArray(png, 0, png.size) }
                .getOrNull()?.let { bmp ->
                    Image(bmp.asImageBitmap(), "Your photo-bound code",
                        Modifier.fillMaxWidth())
                }
            PrimaryButton("Download PNG", Modifier.fillMaxWidth()) {
                saveNote = Downloader.savePng(ctx, png,
                    r.optString("opaque_resolution_id"))
                    ?: "Couldn't save the picture."
            }
            if (saveNote.isNotEmpty()) Text(saveNote,
                style = MaterialTheme.typography.labelSmall, color = AppColors.muted)
            SecondaryButton("Share link", Modifier.fillMaxWidth()) {
                ctx.startActivity(Intent.createChooser(
                    Intent(Intent.ACTION_SEND).apply {
                        type = "text/plain"
                        putExtra(Intent.EXTRA_TEXT, link)
                    }, "Share code"))
            }
            SecondaryButton("Make another", Modifier.fillMaxWidth()) {
                result = null; picker.clear(); saveNote = ""
            }
        }
        Spacer(Modifier.height(XL))
    }

    if (quotaSpent) {
        ConfirmDialog(
            title = "That's all five",
            body = "You've used the five free codes. An account keeps every code "
                 + "you make, lets you switch one off after you've shared it, and "
                 + "shows you where each copy has been scanned.",
            confirmLabel = "Create an account", cancelLabel = "Not now",
            onConfirm = { quotaSpent = false; onWantAccount() },
            onDismiss = { quotaSpent = false })
    }
}

/** Your own codes and scans — the same figures as the website's Usage page, and
 *  deliberately not the developer console's, which is about API keys. */
@Composable
private fun UsageScreen(api: Api, liveVersion: Int = 0) {
    var u by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf("") }
    var page by remember { mutableStateOf(0) }
    LaunchedEffect(liveVersion) {
        runCatching { withContext(Dispatchers.IO) { api.get("/v1/me/usage") } }
            .onSuccess { u = it }
            .onFailure { error = it.message ?: "Couldn't load your usage." }
    }

    Column(Modifier.fillMaxSize().padding(horizontal = MD)) {
        Spacer(Modifier.height(SM))
        Text("Usage", style = MaterialTheme.typography.headlineMedium,
            color = AppColors.ink)
        if (error.isNotEmpty()) ErrorNote(error)
        val d = u
        if (d == null) {
            Row(Modifier.fillMaxWidth().padding(MD),
                horizontalArrangement = Arrangement.Center) {
                CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp,
                    color = Accent)
            }
            return@Column
        }

        val limit = d.optInt("codes_limit", 1000)
        val used = d.optInt("codes_used_this_month")
        val codes = d.optJSONArray("top_codes")
        val total = codes?.length() ?: 0
        val perPage = 6
        val pages = maxOf(1, (total + perPage - 1) / perPage)
        if (page >= pages) page = pages - 1

        LazyColumn(Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(SM)) {
            item {
                SectionTitle("codes this month · ${d.optString("month")}")
                Column(Modifier.fillMaxWidth().background(AppColors.surface).padding(MD),
                    verticalArrangement = Arrangement.spacedBy(XS)) {
                    Text("$used / $limit", fontFamily = Mono,
                        fontWeight = FontWeight.Bold, fontSize = 26.sp,
                        color = AppColors.ink)
                    Text("${d.optInt("codes_remaining_this_month")} left · resets on the 1st",
                        style = MaterialTheme.typography.labelSmall,
                        color = AppColors.muted)
                    // A meter, not a number to interpret.
                    Box(Modifier.fillMaxWidth().height(6.dp)
                        .background(AppColors.line)) {
                        Box(Modifier.fillMaxWidth(
                                (used.toFloat() / limit).coerceIn(0f, 1f))
                            .height(6.dp).background(Accent))
                    }
                    Text("deleting a code does not give the slot back",
                        style = MaterialTheme.typography.labelSmall,
                        color = AppColors.muted)
                }
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(SM)) {
                    StatTile("${d.optInt("codes_live")}", "live", Live, Modifier.weight(1f))
                    StatTile("${d.optInt("codes_off")}", "switched off", Revoked,
                        Modifier.weight(1f))
                }
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(SM)) {
                    StatTile("${d.optInt("scans_total")}", "scans", Blue, Modifier.weight(1f))
                    StatTile("${d.optInt("peak_hour_scans")}", "busiest hour", Accent,
                        Modifier.weight(1f))
                }
            }
            item { UsageBars("scans per day · last 14", d.optJSONArray("scans_by_day"), Accent) }
            item { UsageBars("codes made per day · last 14", d.optJSONArray("codes_by_day"), Live) }
            item { Rule(); SectionTitle("most scanned") }
            items((0 until minOf(perPage, maxOf(0, total - page * perPage))).toList()) { i ->
                val c = codes!!.getJSONObject(page * perPage + i)
                Row(Modifier.fillMaxWidth().padding(vertical = XS),
                    verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(c.optString("opaque_resolution_id"), fontFamily = Mono,
                            fontSize = 12.sp, fontWeight = FontWeight.Bold,
                            color = AppColors.ink)
                        Text("${c.optString("label").ifBlank { "unlabelled" }} · " +
                             c.optString("state"),
                            style = MaterialTheme.typography.labelSmall,
                            color = AppColors.muted)
                    }
                    Text("${c.optInt("scans")}", fontFamily = Mono, fontSize = 13.sp,
                        color = AppColors.ink)
                }
            }
            item { Spacer(Modifier.height(SM)) }
        }
        if (pages > 1) {
            Row(Modifier.fillMaxWidth().padding(vertical = SM),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(SM)) {
                SecondaryButton("Previous", Modifier.weight(1f),
                    enabled = page > 0) { page-- }
                Text("${page + 1} of $pages", fontFamily = Mono, fontSize = 11.sp,
                    color = AppColors.muted)
                SecondaryButton("Next", Modifier.weight(1f),
                    enabled = page < pages - 1) { page++ }
            }
        }
    }
}

/** Bars, not a line: these are counts per day, and a line between two days
 *  implies values in between that never existed. */
@Composable
private fun UsageBars(title: String, series: org.json.JSONArray?, colour: Color) {
    // Zero-filled across a fixed 14-day window, not just the days that have
    // data. Plotting only the populated days made a single day of activity render
    // as one bar spanning the whole width — every bar takes an equal share, so
    // "one bar" and "one day" looked identical. A gap has to read as a gap.
    val counts = buildMap {
        for (i in 0 until (series?.length() ?: 0)) {
            val o = series!!.getJSONObject(i)
            put(o.optString("day"), o.optInt("count"))
        }
    }
    val today = java.time.LocalDate.now()
    val points = (13 downTo 0).map { back ->
        val day = today.minusDays(back.toLong()).toString()
        day.takeLast(2) to (counts[day] ?: 0)
    }
    SectionTitle(title)
    if (points.isEmpty() || points.all { it.second == 0 }) {
        Text("nothing yet", style = MaterialTheme.typography.labelSmall,
            color = AppColors.muted)
        return
    }
    val max = points.maxOf { it.second }.coerceAtLeast(1)
    Row(Modifier.fillMaxWidth().height(96.dp),
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        points.forEach { (label, value) ->
            Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Bottom) {
                Box(Modifier.fillMaxWidth()
                    .height((value.toFloat() / max * 66f).dp.coerceAtLeast(2.dp))
                    .background(if (value > 0) colour else AppColors.line))
                Spacer(Modifier.height(3.dp))
                Text(label, fontFamily = Mono, fontSize = 8.sp, color = AppColors.muted)
            }
        }
    }
}

/* ── codes ────────────────────────────────────────────────────────────────── */

@Composable
private fun CodesScreen(api: Api, liveVersion: Int = 0) {
    var codes by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var error by remember { mutableStateOf("") }
    var pendingRevoke by remember { mutableStateOf<JSONObject?>(null) }
    var opened by remember { mutableStateOf<JSONObject?>(null) }
    var pendingDelete by remember { mutableStateOf<JSONObject?>(null) }
    var deleting by remember { mutableStateOf(false) }
    var page by remember { mutableStateOf(0) }
    var revoking by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    suspend fun reload() {
        runCatching { withContext(Dispatchers.IO) { api.get("/v1/codes") } }
            .onSuccess { r ->
                val a = r.getJSONArray("codes")
                codes = (0 until a.length()).map { a.getJSONObject(it) }
            }
            .onFailure { error = it.message ?: "Couldn't load your codes." }
    }
    LaunchedEffect(liveVersion) { reload() }

    Column(Modifier.fillMaxSize().padding(horizontal = MD)) {
        Spacer(Modifier.height(SM))
        Text("Your codes", style = MaterialTheme.typography.headlineMedium,
            color = AppColors.ink)
        SectionTitle("${codes.size} copies")
        if (error.isNotEmpty()) ErrorNote(error)
        Spacer(Modifier.height(SM))
        // Paged rather than one long scroll, so a hundred copies stay reviewable.
        val perPage = 8
        val pages = maxOf(1, (codes.size + perPage - 1) / perPage)
        if (page >= pages) page = pages - 1
        val shown = codes.drop(page * perPage).take(perPage)

        LazyColumn(Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(SM)) {
            items(shown) { c ->
                CodeRow(c, compact = true, onOpen = { opened = c })
            }
            item { Spacer(Modifier.height(SM)) }
        }
        if (pages > 1) {
            Row(Modifier.fillMaxWidth().padding(vertical = SM),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(SM)) {
                SecondaryButton("Previous", Modifier.weight(1f),
                    enabled = page > 0) { page-- }
                Text("${page + 1} of $pages", fontFamily = Mono, fontSize = 11.sp,
                    color = AppColors.muted)
                SecondaryButton("Next", Modifier.weight(1f),
                    enabled = page < pages - 1) { page++ }
            }
        }
    }

    opened?.let { c ->
        CodeSheet(api, c,
            onRevoke = { pendingRevoke = c; opened = null },
            onDelete = { pendingDelete = c; opened = null },
            onChanged = { scope.launch { reload() } },
            onDismiss = { opened = null })
    }

    pendingDelete?.let { c ->
        ConfirmDialog(
            title = "Delete this code?",
            body = "The picture and this code are removed from your account and "
                 + "cannot be recovered. Anyone scanning a copy is told it has "
                 + "been switched off.",
            confirmLabel = "Delete", destructive = true, busy = deleting,
            requireText = "DELETE",
            onConfirm = {
                deleting = true
                scope.launch {
                    runCatching {
                        withContext(Dispatchers.IO) {
                            api.delete("/v1/codes/${c.getString("credential_id")}")
                        }
                    }.onFailure { error = it.message ?: "Couldn't delete that code." }
                    deleting = false; pendingDelete = null
                    reload()
                }
            },
            onDismiss = { pendingDelete = null })
    }

    pendingRevoke?.let { c ->
        ConfirmDialog(
            title = "Switch this copy off?",
            body = "The next person to scan it is told it has been switched off. " +
                   "Your other copies keep working. This cannot be undone.",
            confirmLabel = "Switch off", destructive = true, busy = revoking,
            onConfirm = {
                revoking = true
                scope.launch {
                    runCatching {
                        withContext(Dispatchers.IO) {
                            api.delete("/v1/shares/${c.getString("share_id")}")
                        }
                    }
                    revoking = false; pendingRevoke = null
                    reload()
                }
            },
            onDismiss = { pendingRevoke = null })
    }
}

/** Everything you can do with one copy, in one place: see it, save it, share
 *  it, read its scan history, switch it off. */
/** The wordmark at the size it deserves on the way in, with the live dot
 *  breathing beside it — the one piece of motion on this screen. */
@Composable
private fun BrandMark() {
    val pulse = rememberInfiniteTransition(label = "brand")
    val dot by pulse.animateFloat(
        initialValue = 0.45f, targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(1400, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse),
        label = "dot")
    val enter = remember { Animatable(0f) }
    LaunchedEffect(Unit) { enter.animateTo(1f, tween(650)) }

    Row(verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.graphicsLayer {
            alpha = enter.value
            translationY = (1f - enter.value) * 18f
        }) {
        Text("Identity", fontFamily = Mono, fontWeight = FontWeight.Bold,
            fontSize = 44.sp, letterSpacing = (-2).sp, color = AppColors.ink)
        Spacer(Modifier.width(SM))
        Box(Modifier.size(11.dp).graphicsLayer { alpha = dot }.background(Live))
    }
}

@Composable
private fun CodeSheet(api: Api, c: JSONObject, onRevoke: () -> Unit,
                      onDelete: () -> Unit, onChanged: () -> Unit,
                      onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val state = c.optString("state")
    val oid = c.optString("opaque_resolution_id")
    val link = "${BuildConfig.API_BASE}/r/$oid"
    var png by remember { mutableStateOf<ByteArray?>(null) }
    var note by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(true) }
    var editing by remember { mutableStateOf(false) }
    var label by remember { mutableStateOf(c.optString("label")) }
    var cap by remember { mutableStateOf(
        c.optInt("max_scans", 0).takeIf { it > 0 }?.toString() ?: "") }
    var days by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    val shareId = c.optString("share_id")

    LaunchedEffect(oid) {
        // The picture lives on the server; the key that decrypts what the code
        // opens never does, so fetching the image reveals nothing about it.
        png = runCatching {
            withContext(Dispatchers.IO) {
                api.getBytes("/v1/photos/${c.optString("photo_id")}.png")
            }
        }.getOrNull()
        loading = false
    }

    Dialog(onDismissRequest = onDismiss) {
        Surface(color = AppColors.bg, shape = RectangleShape) {
            Column(Modifier.fillMaxWidth().heightIn(max = 660.dp)
                .verticalScroll(rememberScrollState()).padding(MD),
                verticalArrangement = Arrangement.spacedBy(SM)) {

                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text(oid, fontFamily = Mono, fontWeight = FontWeight.Bold,
                        fontSize = 15.sp, color = AppColors.ink,
                        modifier = Modifier.weight(1f))
                    StateTag(state)
                }
                Text("${c.optString("label")} · ${c.optInt("scan_count")} scans · "
                     + "decode ${c.optInt("decode_rate")}%",
                    style = MaterialTheme.typography.labelSmall, color = AppColors.muted)

                if (loading) {
                    Row(Modifier.fillMaxWidth().padding(vertical = MD),
                        horizontalArrangement = Arrangement.Center) {
                        CircularProgressIndicator(Modifier.size(20.dp),
                            strokeWidth = 2.dp, color = Accent)
                    }
                }
                png?.let { bytes ->
                    runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size) }
                        .getOrNull()?.let { bmp ->
                            Image(bmp.asImageBitmap(), "This code",
                                Modifier.fillMaxWidth())
                        }
                }

                PrimaryButton("Download PNG", Modifier.fillMaxWidth(),
                    enabled = png != null) {
                    note = png?.let { Downloader.savePng(ctx, it, oid) }
                        ?: "Couldn't save the picture."
                }
                SecondaryButton("Share link", Modifier.fillMaxWidth()) {
                    ctx.startActivity(Intent.createChooser(
                        Intent(Intent.ACTION_SEND).apply {
                            type = "text/plain"
                            putExtra(Intent.EXTRA_TEXT, link)
                        }, "Share code"))
                }
                SecondaryButton("Copy link", Modifier.fillMaxWidth()) {
                    val cb = ctx.getSystemService(android.content.ClipboardManager::class.java)
                    cb?.setPrimaryClip(
                        android.content.ClipData.newPlainText("Identity code", link))
                    note = "Link copied"
                }
                if (note.isNotEmpty()) Text(note,
                    style = MaterialTheme.typography.labelSmall, color = AppColors.muted)

                val log = c.optJSONArray("log")
                if (log != null && log.length() > 0) {
                    Rule()
                    SectionTitle("recent scans")
                    (0 until log.length()).forEach { i ->
                        Text(log.getString(i),
                            style = MaterialTheme.typography.labelSmall,
                            color = AppColors.muted)
                    }
                }

                Rule()
                // Editing is behind a toggle: opening a code should show it, not
                // present five inputs before you've decided to change anything.
                if (state == "active") {
                    SecondaryButton(if (editing) "Done editing" else "Edit this copy",
                        Modifier.fillMaxWidth()) { editing = !editing }
                }
                if (editing && state == "active") {
                    Field("Label", label, { label = it }, placeholder = "LinkedIn")
                    Field("Stop working after this many scans", cap, { cap = it },
                        placeholder = "leave empty for no limit", numeric = true)
                    Field("Stop working in this many days", days, { days = it },
                        placeholder = "leave empty for no expiry", numeric = true)
                    PrimaryButton("Save changes", Modifier.fillMaxWidth(),
                        loading = saving) {
                        saving = true
                        scope.launch {
                            val body = JSONObject().put("label", label)
                                .put("max_scans", cap.toIntOrNull() ?: 0)
                            val d = days.toIntOrNull()
                            body.put("expires_at", if (d == null || d <= 0) "" else
                                java.time.OffsetDateTime.now()
                                    .plusDays(d.toLong()).toString())
                            runCatching {
                                withContext(Dispatchers.IO) {
                                    api.patch("/v1/shares/$shareId", body)
                                }
                            }.onSuccess { note = "Saved"; onChanged() }
                             .onFailure { note = it.message ?: "Couldn't save" }
                            saving = false
                        }
                    }
                }
                if (state == "active") {
                    SecondaryButton("Switch this copy off", Modifier.fillMaxWidth(),
                        color = Revoked) { onRevoke() }
                } else {
                    Text("This copy is already switched off. Scanning it says so, "
                         + "and nothing is decrypted.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = AppColors.muted)
                }
                Rule()
                SecondaryButton("Delete this code and its picture",
                    Modifier.fillMaxWidth(), color = Revoked) { onDelete() }
                Text("Deleting removes the picture and this code from your "
                     + "account. Copies already out there are told they've been "
                     + "switched off.",
                    style = MaterialTheme.typography.labelSmall, color = AppColors.muted)
                PrimaryButton("Done", Modifier.fillMaxWidth()) { onDismiss() }
            }
        }
    }
}

/* ── profile ──────────────────────────────────────────────────────────────── */

@Composable
private fun ProfileScreen(api: Api, me: JSONObject?, onSignedOut: () -> Unit) {
    var name by remember { mutableStateOf(me?.optString("name") ?: "") }
    var legal by remember { mutableStateOf<String?>(null) }
    var showFeedback by remember { mutableStateOf<String?>(null) }
    var confirmSignOut by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var note by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    val themeState = LocalThemeState.current
    var cardName by remember { mutableStateOf("") }
    var cardHeadline by remember { mutableStateOf("") }
    var cardEmail by remember { mutableStateOf("") }
    var cardPhone by remember { mutableStateOf("") }
    var cardWebsite by remember { mutableStateOf("") }
    var cardUrl by remember { mutableStateOf("") }
    var cardNote by remember { mutableStateOf("") }
    var cardSaving by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        runCatching { withContext(Dispatchers.IO) { api.get("/v1/me/card") } }
            .onSuccess {
                cardName = it.optString("display_name")
                cardHeadline = it.optString("headline")
                cardEmail = it.optString("email")
                cardPhone = it.optString("phone")
                cardWebsite = it.optString("website")
                cardUrl = it.optString("url")
            }
    }

    Column(Modifier.fillMaxSize().padding(horizontal = MD)
        .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(SM)) {
        Spacer(Modifier.height(SM))
        Text("Profile", style = MaterialTheme.typography.headlineMedium,
            color = AppColors.ink)
        SectionTitle(me?.optString("email") ?: "")

        Spacer(Modifier.height(XS))
        Field("Name", name, { name = it })
        PrimaryButton("Save", Modifier.fillMaxWidth(), loading = saving) {
            saving = true
            scope.launch {
                runCatching {
                    withContext(Dispatchers.IO) {
                        api.post("/v1/me", JSONObject().put("name", name))
                    }
                }.onSuccess { note = "Saved" }
                 .onFailure { note = it.message ?: "Couldn't save" }
                saving = false
            }
        }
        if (note.isNotEmpty()) Text(note, style = MaterialTheme.typography.labelSmall,
            color = AppColors.muted)

        Spacer(Modifier.height(SM)); Rule()
        SectionTitle("your card page")
        Text("This is what a code opens when you use \"Use my card link\". "
             + "Anyone who scans that code can read it, so only fill in what "
             + "you're happy to hand out.",
            style = MaterialTheme.typography.bodyMedium, color = AppColors.muted)
        Field("Name on the card", cardName, { cardName = it },
            placeholder = "Ama Osei")
        Field("One line about you", cardHeadline, { cardHeadline = it },
            placeholder = "Cardiology")
        Field("Email to show", cardEmail, { cardEmail = it },
            placeholder = "leave empty to hide")
        Field("Phone to show", cardPhone, { cardPhone = it },
            placeholder = "leave empty to hide")
        Field("Website", cardWebsite, { cardWebsite = it },
            placeholder = "leave empty to hide")
        PrimaryButton("Save card", Modifier.fillMaxWidth(), loading = cardSaving) {
            cardSaving = true
            scope.launch {
                runCatching {
                    withContext(Dispatchers.IO) {
                        api.put("/v1/me/card", JSONObject()
                            .put("display_name", cardName)
                            .put("headline", cardHeadline)
                            .put("email", cardEmail)
                            .put("phone", cardPhone)
                            .put("website", cardWebsite))
                    }
                }.onSuccess { cardNote = "Card saved" }
                 .onFailure { cardNote = it.message ?: "Couldn't save the card" }
                cardSaving = false
            }
        }
        if (cardUrl.isNotEmpty()) Text(cardUrl, fontFamily = Mono, fontSize = 11.sp,
            color = AppColors.muted)
        if (cardNote.isNotEmpty()) Text(cardNote,
            style = MaterialTheme.typography.labelSmall, color = AppColors.muted)

        Spacer(Modifier.height(SM)); Rule()
        SectionTitle("appearance")
        // The only theme control in the app. It says which way it will go, so
        // it reads the same whichever theme you are in.
        Row(verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth().clickable { themeState?.toggle() }
                .padding(vertical = XS)) {
            Column(Modifier.weight(1f)) {
                Text("Appearance", style = MaterialTheme.typography.bodyMedium,
                    color = AppColors.ink)
                Text(if (themeState?.isDark == true) "Dark" else "Light",
                    style = MaterialTheme.typography.labelSmall,
                    color = AppColors.muted)
            }
            ThemeToggle()
        }

        Spacer(Modifier.height(SM)); Rule()
        SectionTitle("help")
        SecondaryButton("Send feedback", Modifier.fillMaxWidth()) {
            showFeedback = "feedback"
        }
        SecondaryButton("Report a bug", Modifier.fillMaxWidth()) { showFeedback = "bug" }

        Spacer(Modifier.height(SM)); Rule()
        SectionTitle("legal")
        SecondaryButton("Terms of use", Modifier.fillMaxWidth()) { legal = "terms" }
        SecondaryButton("Privacy policy", Modifier.fillMaxWidth()) { legal = "privacy" }

        Spacer(Modifier.height(SM)); Rule()
        SectionTitle("session")
        SecondaryButton("Sign out", Modifier.fillMaxWidth()) { confirmSignOut = true }
        SecondaryButton("Delete account", Modifier.fillMaxWidth(), color = Revoked) {
            confirmDelete = true
        }
        Text("Deleting the account switches off every code you made — scans stop " +
             "working immediately — and removes your photos.",
            style = MaterialTheme.typography.labelSmall, color = AppColors.muted)

        // The footer lives here only, matching the website's single footer.
        Footer()
        Spacer(Modifier.height(XL))
    }

    legal?.let { LegalSheet(it) { legal = null } }
    showFeedback?.let { k -> FeedbackSheet(initialKind = k) { showFeedback = null } }

    if (confirmSignOut) {
        ConfirmDialog(
            title = "Sign out?",
            body = "Your codes keep working. You'll need to sign in again to manage them.",
            confirmLabel = "Sign out", busy = busy,
            onConfirm = {
                busy = true
                scope.launch {
                    withContext(Dispatchers.IO) { api.signOut() }
                    busy = false; confirmSignOut = false; onSignedOut()
                }
            },
            onDismiss = { confirmSignOut = false })
    }
    if (confirmDelete) {
        ConfirmDialog(
            title = "Delete your account?",
            body = "Every code you made will be switched off and stop resolving. " +
                   "Your photos are removed. This cannot be undone.",
            confirmLabel = "Delete account", destructive = true,
            requireText = "DELETE", busy = busy,
            onConfirm = {
                busy = true
                scope.launch {
                    runCatching {
                        withContext(Dispatchers.IO) {
                            api.delete("/v1/me", JSONObject().put("confirm", "DELETE"))
                        }
                    }
                    busy = false; confirmDelete = false; onSignedOut()
                }
            },
            onDismiss = { confirmDelete = false })
    }
}
