package io.github.mwest2020.jarvis.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The read-back that is playing and the cancel that still has to reach the bridge (jarvis-streaming-speech S6). */
class ReadBackCancelsTest {
    private val failed = CancelOutcome.Failed(TurnFailure(FailureKind.NETWORK, "down"))

    @Test
    fun aReadBackPlayedToItsEndLeavesNothingToCancel() {
        val cancels = ReadBackCancels()
        cancels.started("demo", "c-1", 1_000)
        cancels.played()
        assertNull(cancels.notPlayed(1_500))
        assertNull(cancels.outstanding(1_500))
        assertNull(cancels.clearBeforeTurn(2_000) { error("nothing is due") })
    }

    @Test
    fun aStoppedReadBackIsQueuedUntilTheBridgeAnswers() {
        val cancels = ReadBackCancels()
        cancels.started("demo", "c-1", 1_000)
        val item = cancels.notPlayed(1_500)!!
        assertEquals(ReadBackCancels.Item("demo", "c-1", 1_000), item)
        // The first cancel failed: it is still due.
        assertEquals(item, cancels.due(2_000))
        cancels.sent(item)
        assertNull(cancels.due(2_500))
    }

    @Test
    fun aFailedCancelIsSentAgainBeforeTheNextTurnAndThatTurnGoesWhenItArrives() {
        val cancels = ReadBackCancels()
        cancels.started("demo", "c-1", 1_000)
        cancels.notPlayed(1_500)
        val sent = mutableListOf<String>()
        assertNull(cancels.clearBeforeTurn(10_000) { sent += it.id; CancelOutcome.Done(true) })
        assertEquals(listOf("c-1"), sent)
        // Once answered it is not sent a second time.
        assertNull(cancels.clearBeforeTurn(11_000) { error("already cancelled") })
    }

    @Test
    fun theTurnIsRefusedWhileTheCancelStillFails() {
        val cancels = ReadBackCancels()
        cancels.started("demo", "c-1", 1_000)
        cancels.notPlayed(1_500)
        val refused = cancels.clearBeforeTurn(10_000) { failed }!!
        assertEquals(ReadBack.TURN_REFUSED, refused.message)
        assertEquals(
            "I couldn't cancel the last question on the bridge, so I'm not sending this one. Try again in a minute.",
            refused.message,
        )
        // Still queued for the turn after.
        assertEquals("c-1", cancels.due(20_000)?.id)
    }

    @Test
    fun theQueuedCancelIsDroppedOnceTheBridgeNoLongerKeepsTheReadBack() {
        val cancels = ReadBackCancels(ttlMs = 60_000)
        cancels.started("demo", "c-1", 1_000)
        cancels.notPlayed(2_000)
        assertEquals("c-1", cancels.due(60_999)?.id)
        assertNull(cancels.due(61_000))
        assertNull(cancels.clearBeforeTurn(61_000) { error("expired, never sent") })
    }

    @Test
    fun aTapThenTheScreenClosingStillCancels() {
        val cancels = ReadBackCancels()
        cancels.started("demo", "c-1", 1_000)
        cancels.notPlayed(1_500)            // the tap: queued, its cancel in flight
        assertEquals("c-1", cancels.outstanding(1_600)?.id)   // onCleared sends it again
    }

    @Test
    fun theScreenClosingWhileAReadBackPlaysCancelsIt() {
        val cancels = ReadBackCancels()
        cancels.started("demo", "c-2", 1_000)
        assertEquals("c-2", cancels.outstanding(1_200)?.id)
        assertEquals("c-2", cancels.due(1_300)?.id)
    }

    @Test
    fun anAnswerForAnOlderReadBackDoesNotDropANewerOne() {
        val cancels = ReadBackCancels()
        cancels.started("demo", "c-1", 1_000)
        val old = cancels.notPlayed(1_100)!!
        cancels.started("demo", "c-2", 2_000)
        cancels.notPlayed(2_100)
        cancels.sent(old)
        assertEquals("c-2", cancels.due(2_200)?.id)
    }
}
