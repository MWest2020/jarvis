package io.github.mwest2020.jarvis.core

import org.junit.Assert.assertEquals
import org.junit.Test

class TalkGestureTest {
    @Test
    fun holdingSendsOnRelease() {
        val gesture = TalkGesture(holdThresholdMs = 500)
        assertEquals(TalkAction.START_RECORDING, gesture.onPress(Phase.IDLE, 0))
        assertEquals(TalkAction.STOP_AND_SEND, gesture.onRelease(Phase.RECORDING, 2_000))
    }

    @Test
    fun aTapKeepsRecordingUntilTheNextTap() {
        val gesture = TalkGesture(holdThresholdMs = 500)
        assertEquals(TalkAction.START_RECORDING, gesture.onPress(Phase.IDLE, 0))
        assertEquals(TalkAction.NONE, gesture.onRelease(Phase.RECORDING, 120))
        assertEquals(TalkAction.STOP_AND_SEND, gesture.onPress(Phase.RECORDING, 4_000))
        assertEquals(TalkAction.NONE, gesture.onRelease(Phase.WAITING, 4_100))
    }

    @Test
    fun nothingHappensWhileWaitingAndAPressStopsPlayback() {
        val gesture = TalkGesture()
        assertEquals(TalkAction.NONE, gesture.onPress(Phase.WAITING, 0))
        assertEquals(TalkAction.NONE, gesture.onRelease(Phase.WAITING, 900))
        assertEquals(TalkAction.STOP_PLAYBACK, gesture.onPress(Phase.PLAYING, 1_000))
        assertEquals(TalkAction.NONE, gesture.onRelease(Phase.IDLE, 2_000))
    }

    @Test
    fun aReleaseAfterAFailedStartDoesNothing() {
        val gesture = TalkGesture(holdThresholdMs = 500)
        assertEquals(TalkAction.START_RECORDING, gesture.onPress(Phase.IDLE, 0))
        // The microphone could not be opened, so the phase is still IDLE.
        assertEquals(TalkAction.NONE, gesture.onRelease(Phase.IDLE, 2_000))
    }

    @Test
    fun holdingLastsFromThePressToItsRelease() {
        val gesture = TalkGesture(holdThresholdMs = 500)
        assertEquals(false, gesture.holding)
        gesture.onPress(Phase.IDLE, 0)
        assertEquals(true, gesture.holding)
        // A tap: released, still recording, no longer held (jarvis-silence-send H7).
        gesture.onRelease(Phase.RECORDING, 120)
        assertEquals(false, gesture.holding)
        gesture.onPress(Phase.RECORDING, 4_000)
        assertEquals(false, gesture.holding)
    }
}
