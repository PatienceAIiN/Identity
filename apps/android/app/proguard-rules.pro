# Identity — R8 rules.
#
# Kept deliberately small: the app has no reflection-based serialisation, so
# only the pieces other tools reach into need protecting.

# OkHttp / Okio ship their own consumer rules; these silence platform-only
# references that R8 cannot resolve on Android and that are never executed.
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**

# org.json is provided by the platform at runtime.
-dontwarn org.json.**

# Compose keeps what it needs via its own rules; nothing extra required.

# Line numbers make a crash report from a shrunk build readable, which is the
# whole point of collecting them.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

# Tink (via androidx.security:security-crypto) references Error Prone
# annotations that are compile-only and never present at runtime.
-dontwarn com.google.errorprone.annotations.**
-dontwarn javax.annotation.**
