// Versions are pinned in gradle/libs.versions.toml; nothing here resolves "latest".
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.jvm) apply false
    alias(libs.plugins.kotlin.compose) apply false
}
