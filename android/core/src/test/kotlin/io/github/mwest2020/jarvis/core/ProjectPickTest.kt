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
