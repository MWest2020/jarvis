import org.gradle.api.artifacts.component.ModuleComponentIdentifier

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "io.github.mwest2020.jarvis"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "io.github.mwest2020.jarvis"
        minSdk = 29
        targetSdk = 37
        // A sideloaded update must have a higher versionCode; CI's run number gives that.
        versionCode = providers.environmentVariable("GITHUB_RUN_NUMBER").map { it.toInt() }.getOrElse(1)
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            // No release signing yet: it waits for a key only Mark can create
            // (openspec/changes/add-android-client, open question 1).
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
    }

    lint {
        abortOnError = true
        checkReleaseBuilds = false
    }
}

dependencies {
    implementation(project(":core"))
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.activity.compose)
    implementation(libs.kotlinx.coroutines.android)
}

// Design D9 (sovereignty): the app may ship AndroidX, Kotlin and kotlinx only. This fails
// the build when anything else reaches the runtime classpath, Google Play services,
// Firebase and analytics SDKs included. The exceptions are small annotation-only or
// interface-only artifacts that AndroidX itself depends on.
val allowedGroupPrefixes = listOf("androidx.")
val allowedGroups = setOf("org.jetbrains.kotlin", "org.jetbrains.kotlinx")
val allowedModules = setOf(
    "org.jetbrains:annotations",
    "org.jspecify:jspecify",
    "com.google.guava:listenablefuture",
)

tasks.register("checkSovereignDependencies") {
    group = "verification"
    description = "Fails when a dependency outside AndroidX/Kotlin is on a runtime classpath."
    val container = configurations
    doLast {
        val seen = sortedSetOf<String>()
        val refused = sortedSetOf<String>()
        for (name in listOf("debugRuntimeClasspath", "releaseRuntimeClasspath")) {
            container.getByName(name).incoming.resolutionResult.allComponents
                .mapNotNull { it.id as? ModuleComponentIdentifier }
                .forEach { id ->
                    val module = "${id.group}:${id.module}"
                    seen += module
                    val ok = allowedGroupPrefixes.any { id.group.startsWith(it) } ||
                        id.group in allowedGroups || module in allowedModules
                    if (!ok) refused += "$module:${id.version}"
                }
        }
        if (refused.isNotEmpty()) {
            throw GradleException(
                "Dependencies outside AndroidX/Kotlin on the runtime classpath (design D9):\n" +
                    refused.joinToString("\n") { "  $it" },
            )
        }
        println("checkSovereignDependencies: ${seen.size} modules, all AndroidX/Kotlin")
    }
}

tasks.named("check") {
    dependsOn("checkSovereignDependencies")
}
