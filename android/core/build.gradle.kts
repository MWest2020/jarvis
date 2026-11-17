import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// Everything that can be tested without a phone: the request, the error mapping, the WAV
// header, the tailnet address check, the press logic. No Android classes in here.
plugins {
    alias(libs.plugins.kotlin.jvm)
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    compilerOptions {
        jvmTarget = JvmTarget.JVM_17
    }
}

dependencies {
    testImplementation(libs.junit)
}

tasks.test {
    testLogging {
        // One line per test in the CI log, so a green run shows what it ran.
        events("passed", "failed", "skipped")
    }
}
