plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "in.photobind.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "in.photobind.app"
        minSdk = 26
        targetSdk = 34
        // versionName is what people see and stays 1.0. versionCode is internal
        // and MUST increase for every published build: the updater treats
        // "same code" as "already up to date", so a build shipped without a
        // bump reaches new downloads only and never existing installs.
        versionCode = 17
        versionName = "1.0"
        // Point at your API. Default is the Android emulator's host alias.
        buildConfigField("String", "API_BASE",
            "\"${project.findProperty("apiBase") ?: "http://10.0.2.2:8000"}\"")
        // Google sign-in uses the WEB client id as the audience, even on
        // Android — Credential Manager returns an ID token minted for it.
        buildConfigField("String", "GOOGLE_WEB_CLIENT_ID",
            "\"${project.findProperty("googleClientId") ?: ""}\"")

        // ML Kit ships native libraries for four ABIs. x86/x86_64 exist for
        // emulators only and cost ~12 MB in a file people download over mobile
        // data, so this APK carries the two ARM ABIs every real phone uses.
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }
    }

    signingConfigs {
        create("release") {
            // Dev keystore so `assembleRelease` produces an installable APK.
            // REPLACE with your own before publishing: an Android app can never
            // change signing keys after release.
            storeFile = file(project.findProperty("keystoreFile") ?: "dev-release.jks")
            storePassword = (project.findProperty("keystorePassword") ?: "photobind") as String
            keyAlias = (project.findProperty("keystoreAlias") ?: "identity") as String
            keyPassword = (project.findProperty("keystoreKeyPassword") ?: "photobind") as String
            // Sign with every scheme the platform understands. Shipping v2
            // alone is a signal to Play Protect that the build is unusual.
            enableV1Signing = true
            enableV2Signing = true
            enableV3Signing = true
            enableV4Signing = false      // v4 needs a side file; not used here
        }
    }

    buildTypes {
        release {
            // Shrunk and optimised. An unminified sideloaded APK reads as a
            // repackaged app to Play Protect's heuristics.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"),
                          "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("release")
            isDebuggable = false
        }
    }

    // Self-update needs REQUEST_INSTALL_PACKAGES. It is also the strongest
    // Play-Protect signal a sideloaded app can carry, so dropping it was tried
    // as a cure for "harmful app blocked" — at the cost of updating inside the
    // app, which is the flow this product wants. It is back on by default; the
    // remaining lever against that warning is a real signing key, not this
    // permission. Build with -PotaInApp=false to hand updates to the browser.
    val otaInApp = (project.findProperty("otaInApp") ?: "true") == "true"
    defaultConfig {
        buildConfigField("boolean", "OTA_IN_APP", otaInApp.toString())
        manifestPlaceholders["otaPermission"] =
            if (otaInApp) "android.permission.REQUEST_INSTALL_PACKAGES"
            else "android.permission.INTERNET"     // harmless duplicate
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlin {
        // Kotlin 2.4 removed the kotlinOptions DSL.
        compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
    }
    buildFeatures { compose = true; buildConfig = true }
    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
        // Uncompressed libs install faster and avoid an extra copy on device.
        jniLibs.useLegacyPackaging = false
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.4")
    implementation("androidx.activity:activity-compose:1.9.1")
    implementation(platform("androidx.compose:compose-bom:2026.06.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    // 1.4 is where Material 3 Expressive is stable: MaterialExpressiveTheme,
    // MotionScheme.expressive(), button groups, the expressive loading
    // indicator, and shape morphing.
    implementation("androidx.compose.material3:material3:1.4.0")
    // Google's own Material Symbols, so the tab bar and controls use the icons
    // people already recognise instead of shapes drawn by hand. R8 keeps only
    // the handful actually referenced.
    implementation("androidx.compose.material:material-icons-extended:1.7.8")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // Credential Manager is the current sign-in API; the legacy Google
    // Sign-In SDK is deprecated.
    implementation("androidx.credentials:credentials:1.3.0")
    implementation("androidx.credentials:credentials-play-services-auth:1.3.0")
    implementation("com.google.android.libraries.identity.googleid:googleid:1.1.1")
    // Camera + on-device barcode reading for the scanner. ML Kit runs locally;
    // the image never leaves the phone.
    implementation("androidx.camera:camera-camera2:1.3.4")
    implementation("androidx.camera:camera-lifecycle:1.3.4")
    implementation("androidx.camera:camera-view:1.3.4")
    implementation("com.google.mlkit:barcode-scanning:17.3.0")
    // Animated splash that hands over to the app's own first frame.
    implementation("androidx.core:core-splashscreen:1.0.1")
    implementation("org.json:json:20240303")
}
