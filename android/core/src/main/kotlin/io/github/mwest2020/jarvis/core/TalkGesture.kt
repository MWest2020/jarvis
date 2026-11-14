package io.github.mwest2020.jarvis.core

enum class Phase { IDLE, RECORDING, WAITING, PLAYING }

enum class TalkAction { NONE, START_RECORDING, STOP_AND_SEND, STOP_PLAYBACK }

/**
 * The one button, two ways to use it. Hold it and let go: the recording is sent on release.
 * Tap it: it keeps recording until the next tap. A press shorter than [holdThresholdMs]
 * counts as a tap.
 */
class TalkGesture(private val holdThresholdMs: Long = 500) {
    private var startedAt: Long? = null

    fun onPress(phase: Phase, nowMs: Long): TalkAction = when (phase) {
        Phase.IDLE -> {
            startedAt = nowMs
            TalkAction.START_RECORDING
        }
        Phase.RECORDING -> {
            startedAt = null
            TalkAction.STOP_AND_SEND
        }
        Phase.PLAYING -> TalkAction.STOP_PLAYBACK
        Phase.WAITING -> TalkAction.NONE
    }

    fun onRelease(phase: Phase, nowMs: Long): TalkAction {
        val started = startedAt ?: return TalkAction.NONE
        startedAt = null
        return if (phase == Phase.RECORDING && nowMs - started >= holdThresholdMs) {
            TalkAction.STOP_AND_SEND
        } else {
            TalkAction.NONE
        }
    }
}
