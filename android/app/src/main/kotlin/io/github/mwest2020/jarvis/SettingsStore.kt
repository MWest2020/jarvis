package io.github.mwest2020.jarvis

import android.content.Context
import io.github.mwest2020.jarvis.core.Endpointing
import io.github.mwest2020.jarvis.core.ProjectMenu
import io.github.mwest2020.jarvis.core.ProjectSetting

/**
 * The settings that are not secret: the bridge address, the projects setting, the last pick, the default project,
 * the last list the bridge gave (shown until a refresh brings a new one), and sending when Mark stops talking.
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

    /** "Send when I stop talking" (change jarvis-silence-send, H8): on until switched off. */
    var silenceSend: Boolean
        get() = prefs.getBoolean("silence_send", true)
        set(value) = prefs.edit().putBoolean("silence_send", value).apply()

    /** The silence that sends a tapped recording, 1.5 to 6 s in half seconds; 3 s until set. */
    var silenceSeconds: Double
        get() = Endpointing.silenceSeconds(
            prefs.getFloat("silence_seconds", Endpointing.DEFAULT_SILENCE_SECONDS.toFloat()).toDouble(),
        )
        set(value) = prefs.edit().putFloat("silence_seconds", Endpointing.silenceSeconds(value).toFloat()).apply()
}
