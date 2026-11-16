package io.github.mwest2020.jarvis.core

/**
 * Which project the app talks to. It is never the first of the list by accident: a turn
 * sent to the wrong repository is worse than one more tap.
 */
object ProjectPick {
    /**
     * The project to select, or "" when the user has to pick one:
     * 1. [remembered], the last project picked by hand, when the list still has it;
     * 2. otherwise [default], the "Default project" setting, when the list has it;
     * 3. otherwise nothing.
     */
    fun choose(projects: List<String>, remembered: String, default: String): String {
        if (remembered.isNotEmpty() && remembered in projects) return remembered
        val wanted = default.trim()
        if (wanted.isEmpty()) return ""
        return projects.firstOrNull { it == wanted } ?: projects.firstOrNull { it.equals(wanted, ignoreCase = true) }.orEmpty()
    }

    /**
     * The order the bridge's list is shown in: alphabetical, upper and lower case together.
     * `GET /projects` carries names only, no date to sort by.
     */
    fun order(names: List<String>): List<String> =
        names.sortedWith(String.CASE_INSENSITIVE_ORDER.thenBy { it })
}
