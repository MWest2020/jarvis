package io.github.mwest2020.jarvis

import android.content.Context

/** The settings that are not secret: the bridge address, the project names, the last pick. */
class SettingsStore(context: Context) {
    private val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var bridgeUrl: String
        get() = prefs.getString("bridge_url", "").orEmpty()
        set(value) = prefs.edit().putString("bridge_url", value).apply()

    var projects: List<String>
        get() = parseProjects(prefs.getString("projects", "").orEmpty())
        set(value) = prefs.edit().putString("projects", value.joinToString(",")).apply()

    var project: String
        get() = prefs.getString("project", "").orEmpty()
        set(value) = prefs.edit().putString("project", value).apply()

    companion object {
        fun parseProjects(text: String): List<String> =
            text.split(',', '\n', ' ').map { it.trim() }.filter { it.isNotEmpty() }.distinct()
    }
}
