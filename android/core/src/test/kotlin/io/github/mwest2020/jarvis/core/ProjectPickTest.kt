package io.github.mwest2020.jarvis.core

import org.junit.Assert.assertEquals
import org.junit.Test

/** The project the app selects: the last pick, the default setting, or none. Never the first. */
class ProjectPickTest {
    private val projects = listOf("Applied_Cryptography_Chatappp", "bazibo", "jarvis")

    @Test
    fun nothingRememberedAndNoDefaultSelectsNothing() {
        assertEquals("", ProjectPick.choose(projects, remembered = "", default = ""))
        assertEquals("", ProjectPick.choose(projects, remembered = "", default = "   "))
    }

    @Test
    fun aListOfExactlyOneProjectIsSelected() {
        assertEquals("only", ProjectPick.choose(listOf("only"), remembered = "", default = ""))
        assertEquals("only", ProjectPick.choose(listOf("only"), remembered = "gone", default = "other"))
        assertEquals("", ProjectPick.choose(listOf("only", "second"), remembered = "", default = ""))
        assertEquals("", ProjectPick.choose(emptyList(), remembered = "", default = ""))
    }

    @Test
    fun theLastPickWins() {
        assertEquals("jarvis", ProjectPick.choose(projects, remembered = "jarvis", default = "bazibo"))
    }

    @Test
    fun aPickThatLeftTheListFallsBackToTheDefaultOrNothing() {
        assertEquals("bazibo", ProjectPick.choose(projects, remembered = "gone", default = "bazibo"))
        assertEquals("", ProjectPick.choose(projects, remembered = "gone", default = ""))
        assertEquals("", ProjectPick.choose(emptyList(), remembered = "jarvis", default = "bazibo"))
    }

    @Test
    fun theDefaultIsUsedWhenNothingIsRemembered() {
        assertEquals("bazibo", ProjectPick.choose(projects, remembered = "", default = " bazibo "))
        assertEquals("jarvis", ProjectPick.choose(projects, remembered = "", default = "Jarvis"))
        assertEquals("", ProjectPick.choose(projects, remembered = "", default = "not-on-the-bridge"))
    }

    @Test
    fun anExactNameBeatsOneThatDiffersInCase() {
        assertEquals("Demo", ProjectPick.choose(listOf("demo", "Demo"), remembered = "", default = "Demo"))
    }

    @Test
    fun theListIsShownAlphabeticallyIgnoringCase() {
        assertEquals(
            listOf("Applied_Cryptography_Chatappp", "bazibo", "Billbird", "jarvis", "Wanderer"),
            ProjectPick.order(listOf("Wanderer", "jarvis", "Billbird", "bazibo", "Applied_Cryptography_Chatappp")),
        )
        assertEquals(listOf("A", "a", "b"), ProjectPick.order(listOf("b", "a", "A")))
    }
}

/** jarvis-general-conversation D50: the General entry for the home project. */
class ProjectMenuTest {
    private val fromBridge = listOf("bazibo", "jarvis", "jarvis-home", "wordsworth")
    private val home = ProjectMenu.DEFAULT_HOME

    @Test
    fun generalIsFirstAndTheHomeProjectIsNotListedTwice() {
        val entries = ProjectMenu.entries(fromBridge, home)
        assertEquals(ProjectEntry("jarvis-home", "General", general = true), entries.first())
        assertEquals(listOf("General", "bazibo", "jarvis", "wordsworth"), entries.map { it.label })
        assertEquals(listOf("jarvis-home", "bazibo", "jarvis", "wordsworth"), entries.map { it.project })
    }

    @Test
    fun generalIsShownEvenWhenTheListLacksTheHomeProject() {
        val entries = ProjectMenu.entries(listOf("bazibo"), home)
        assertEquals(listOf("jarvis-home", "bazibo"), entries.map { it.project })
        assertEquals(listOf("jarvis-home"), ProjectMenu.entries(emptyList(), " jarvis-home ").map { it.project })
    }

    @Test
    fun theListsSpellingOfTheHomeProjectIsSent() {
        val entries = ProjectMenu.entries(listOf("Jarvis-Home", "bazibo"), "jarvis-home")
        assertEquals(listOf("Jarvis-Home", "bazibo"), entries.map { it.project })
        assertEquals("General", entries.first().label)
    }

    @Test
    fun aBlankHomeProjectMeansNoGeneral() {
        assertEquals(fromBridge, ProjectMenu.entries(fromBridge, "  ").map { it.label })
        assertEquals(fromBridge, ProjectMenu.entries(fromBridge, "").map { it.project })
    }

    @Test
    fun generalIsSelectedWhenNothingIsRememberedAndNoDefaultIsSet() {
        assertEquals("jarvis-home", ProjectPick.choose(fromBridge, remembered = "", default = "", home = home))
        assertEquals("jarvis-home", ProjectPick.choose(listOf("bazibo"), remembered = "", default = "", home = home))
        assertEquals("jarvis-home", ProjectPick.choose(emptyList(), remembered = "", default = "", home = home))
    }

    @Test
    fun theLastPickAndTheDefaultComeBeforeGeneral() {
        assertEquals("jarvis", ProjectPick.choose(fromBridge, remembered = "jarvis", default = "bazibo", home = home))
        assertEquals("bazibo", ProjectPick.choose(fromBridge, remembered = "", default = "bazibo", home = home))
        assertEquals("bazibo", ProjectPick.choose(fromBridge, remembered = "gone", default = "Bazibo", home = home))
        assertEquals("jarvis-home", ProjectPick.choose(fromBridge, remembered = "jarvis-home", default = "bazibo", home = home))
        assertEquals("jarvis-home", ProjectPick.choose(fromBridge, remembered = "gone", default = "not-there", home = home))
    }

    @Test
    fun withoutGeneralTheOldRulesHold() {
        assertEquals("", ProjectPick.choose(fromBridge, remembered = "", default = "", home = ""))
        assertEquals("only", ProjectPick.choose(listOf("only"), remembered = "", default = "", home = ""))
    }
}
