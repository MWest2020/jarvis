package io.github.mwest2020.jarvis

import android.content.Context
import io.github.mwest2020.jarvis.core.ProjectMenu
import io.github.mwest2020.jarvis.core.ProjectSetting

/**
 * The settings that are not secret: the bridge address, the projects setting, the last pick, the default project,
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

    /**
     * The last project picked by hand; it survives restarts and refreshes. Only a tap on a
     * project writes it. (The older key "project" also held the app's own first-of-the-list
     * choice, so it is not read any more and is removed here.)
     */
    var pickedProject: String
        get() = prefs.getString("picked_project", "").orEmpty()
        set(value) = prefs.edit().putString("picked_project", value).remove("project").apply()

    /** The "Default project" setting: selected when no pick is remembered. May be empty. */
    var defaultProject: String
        get() = prefs.getString("default_project", "").orEmpty()
        set(value) = prefs.edit().putString("default_project", value.trim()).apply()

    /**
     * The "Home project" setting: the project the General entry talks to. [ProjectMenu.DEFAULT_HOME]
     * until it is saved; saved blank, there is no General entry.
     */
    var homeProject: String
        get() = if (prefs.contains("home_project")) prefs.getString("home_project", "").orEmpty() else ProjectMenu.DEFAULT_HOME
        set(value) = prefs.edit().putString("home_project", value.trim()).apply()
}
