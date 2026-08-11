plugins {
    id("com.android.application") version "8.13.2" apply false
    id("org.jetbrains.kotlin.android") version "2.4.10" apply false
    // Kotlin 2.x moved the Compose compiler out of kotlinCompilerExtensionVersion
    // and into its own plugin. Material 3 Expressive needs material3 1.4, which
    // needs a Compose runtime that only this toolchain can compile.
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.10" apply false
}
