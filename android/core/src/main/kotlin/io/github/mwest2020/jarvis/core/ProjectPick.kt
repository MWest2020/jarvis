package io.github.mwest2020.jarvis.core

/**
 * One entry of the picker: the [project] a turn is sent for, and the [label] shown. The
 * General entry (jarvis-general-conversation D50) is the home project under another label.
 */
data class ProjectEntry(val project: String, val label: String, val general: Boolean = false)

/**
 * The picker's entries: "General" first, for the home project, then the other projects.
 * A blank home project means no General entry: the picker is the plain list.
 */
object ProjectMenu {
    const val GENERAL_LABEL = "General"

    /** The Home project setting's value until Mark changes it. */
    const val DEFAULT_HOME = "jarvis-home"

    /**
     * The home project's name as the list spells it: the exact name, else one that differs
     * only in case, else the setting as typed. "" when there is no home project.
     */
    fun homeName(projects: List<String>, home: String): String {
        val wanted = home.trim()
        if (wanted.isEmpty()) return ""
        return projects.firstOrNull { it == wanted }
            ?: projects.firstOrNull { it.equals(wanted, ignoreCase = true) }
            ?: wanted
    }

    /** General (when there is a home project), then every other project in the given order. */
    fun entries(projects: List<String>, home: String): List<ProjectEntry> {
        val name = homeName(projects, home)
        val rest = projects.filter { it != name }.map { ProjectEntry(it, it) }
        return if (name.isEmpty()) rest else listOf(ProjectEntry(name, GENERAL_LABEL, general = true)) + rest
    }
}

/**
 * Which project the app talks to. It is never the first of a longer list by accident: a
 * turn sent to the wrong repository is worse than one more tap.
 */
object ProjectPick {
    /**
     * The project to select, or "" when the user has to pick one. Over the picker's entries
     * ([ProjectMenu.entries], General included when [home] is set):
     * 0. exactly one entry: that one, there is no choice;
     * 1. [remembered], the last project picked by hand, when it is still an entry;
     * 2. otherwise [default], the "Default project" setting, when it is an entry;
     * 3. otherwise General, the home project, when there is one;
     * 4. otherwise nothing.
     */
    fun choose(projects: List<String>, remembered: String, default: String, home: String = ""): String {
        val names = ProjectMenu.entries(projects, home).map { it.project }
        if (names.size == 1) return names.single()
        if (remembered.isNotEmpty() && remembered in names) return remembered
        val wanted = default.trim()
        if (wanted.isNotEmpty()) {
            val match = names.firstOrNull { it == wanted } ?: names.firstOrNull { it.equals(wanted, ignoreCase = true) }
            if (match != null) return match
        }
        return ProjectMenu.homeName(projects, home)
    }

    /**
     * The order the bridge's list is shown in: alphabetical, upper and lower case together.
     * `GET /projects` carries names only, no date to sort by.
     */
    fun order(names: List<String>): List<String> =
        names.sortedWith(String.CASE_INSENSITIVE_ORDER.thenBy { it })
}
