package io.github.mwest2020.jarvis

import android.content.Context
import io.github.mwest2020.jarvis.core.ProjectSetting

/**
 * The settings that are not secret: the bridge address, the projects setting, the last pick,
 * and the last list the bridge gave (shown until a refresh brings a new one).
 */
class SettingsStore(context: Context) {
    private val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var bridgeUrl: String
        get() = prefs.getString("bridge_url", "").orEmpty()
        set(value) = prefs.edit().putString("bridge_url", value).apply()

    /** Typed names, or [ProjectSetting.FromBridge] for an empty field or `*`. */
    var projectSetting: ProjectSetting
        get() = ProjectSetting.parse(prefs.getString("projects", "").orEmpty())
        set(value) = prefs.edit().putString("projects", ProjectSetting.format(value)).apply()

    /** The bridge's last answer to `GET /projects`. Names never contain a newline. */
    var fetchedProjects: List<String>
        get() = prefs.getString("fetched_projects", "").orEmpty().split('\n').filter { it.isNotEmpty() }
        set(value) = prefs.edit().putString("fetched_projects", value.joinToString("\n")).apply()

    var project: String
        get() = prefs.getString("project", "").orEmpty()
        set(value) = prefs.edit().putString("project", value).apply()
}
