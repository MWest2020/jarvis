package io.github.mwest2020.jarvis.core

/**
 * One entry of the picker: the [project] a turn is sent for, and the [label] shown. The
 * General entry (jarvis-general-conversation D50) is the home project under another label.
 */
data class ProjectEntry(val project: String, val label: String, val general: Boolean = false)

/**
 * The picker's entries: "General" first, for the home project, then the other projects.
 * General is offered only when the home project is in the list (from the bridge, or typed);
 * otherwise, and with a blank home project, the picker is the plain list.
 */
object ProjectMenu {
    const val GENERAL_LABEL = "General"

    /** The Home project setting's value until Mark changes it. */
    const val DEFAULT_HOME = "jarvis-home"

    /**
     * The home project's name as the list spells it: the exact name, else one that differs
     * only in case. "" when there is no home project or the list does not have it.
     */
    fun homeName(projects: List<String>, home: String): String {
        val wanted = home.trim()
        if (wanted.isEmpty()) return ""
        return projects.firstOrNull { it == wanted }
            ?: projects.firstOrNull { it.equals(wanted, ignoreCase = true) }
            ?: ""
    }

    /** General (when the list has the home project), then every other project in the given order. */
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
     * The project to select, or "" when the user has to pick one:
     * 0. exactly one project in the list: that one, there is no choice (General, when it is
     *    there, is that same project under another label);
     * 1. [remembered], the last project picked by hand, when the list still has it;
     * 2. otherwise [default], the "Default project" setting, when the list has it;
     * 3. otherwise General, the home project, when the list has it;
     * 4. otherwise nothing.
     */
    fun choose(projects: List<String>, remembered: String, default: String, home: String = ""): String {
        if (projects.size == 1) return projects.single()
        if (remembered.isNotEmpty() && remembered in projects) return remembered
        val wanted = default.trim()
        if (wanted.isNotEmpty()) {
            val match = projects.firstOrNull { it == wanted } ?: projects.firstOrNull { it.equals(wanted, ignoreCase = true) }
            if (match != null) return match
        }
        return ProjectMenu.homeName(projects, home)
    }

    /**
     * After a refresh: the [current] selection stays while the list still has it (D56); only
     * otherwise is it chosen again as at start.
     */
    fun keep(current: String, projects: List<String>, remembered: String, default: String, home: String = ""): String =
        if (current.isNotEmpty() && current in projects) current else choose(projects, remembered, default, home)

    /**
     * The order the bridge's list is shown in: alphabetical, upper and lower case together.
     * `GET /projects` carries names only, no date to sort by.
     */
    fun order(names: List<String>): List<String> =
        names.sortedWith(String.CASE_INSENSITIVE_ORDER.thenBy { it })
}
