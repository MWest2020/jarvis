package io.github.mwest2020.jarvis.core

import kotlin.math.floor
import kotlin.math.roundToLong
import kotlin.math.sqrt

/**
 * How the end of a spoken question is found (change jarvis-silence-send, H1 to H9). Times are
 * milliseconds of audio; loudness is the RMS of 16-bit samples (0 to 32768).
 */
data class Endpointing(
    /** The silence after speech that sends the recording (the `silence_seconds` setting). */
    val silenceMs: Long = (DEFAULT_SILENCE_SECONDS * 1_000).roundToLong(),
    /** Speech needed in one utterance before a silence sends it (H3, H5). */
    val minSpeechMs: Long = MIN_SPEECH_MS,
    /** The start of the recording that gives the noise floor (H1). */
    val calibrationMs: Long = 300,
    /** No speech for this long since the start or the last sound: the recording is dropped (H4). */
    val nothingHeardMs: Long = 10_000,
    /** The countdown shows once the silence after speech has lasted this long (H6). */
    val countdownAfterMs: Long = 500,
    /** What is kept after the last speech frame when the recording is sent (H9). */
    val tailMs: Long = 500,
    /** A frame is speech at this many times the noise floor (3 is about +9.5 dB). */
    val speechRatio: Double = 3.0,
    /** The floor never goes below this: a muted microphone does not make every breath speech. */
    val minFloor: Double = 50.0,
    /** The first floor is at most this, so speech from the first frame on is not taken for the room. */
    val maxInitialFloor: Double = 600.0,
) {
    companion object {
        const val DEFAULT_SILENCE_SECONDS = 3.0
        const val MIN_SILENCE_SECONDS = 1.5
        const val MAX_SILENCE_SECONDS = 6.0
        const val SILENCE_STEP_SECONDS = 0.5
        const val MIN_SPEECH_MS = 600L

        /** A spoken yes: the bridge's own minimum for a confirmation (D36), H5. */
        const val MIN_YES_MS = 300L

        /** The setting as used: 1.5 to 6 s in half seconds; anything unreadable is the default. */
        fun silenceSeconds(value: Double): Double {
            if (value.isNaN()) return DEFAULT_SILENCE_SECONDS
            val clamped = value.coerceIn(MIN_SILENCE_SECONDS, MAX_SILENCE_SECONDS)
            return floor(clamped / SILENCE_STEP_SECONDS + 0.5) * SILENCE_STEP_SECONDS
        }

        /** "3 s", "1.5 s". */
        fun label(seconds: Double): String {
            val s = silenceSeconds(seconds)
            return if (s == floor(s)) "${s.toLong()} s" else "$s s"
        }

        /** The settings for one recording: the silence, and the shorter minimum when a yes is expected. */
        fun forRecording(silenceSeconds: Double, yesExpected: Boolean): Endpointing = Endpointing(
            silenceMs = (silenceSeconds(silenceSeconds) * 1_000).roundToLong(),
            minSpeechMs = if (yesExpected) MIN_YES_MS else MIN_SPEECH_MS,
        )
    }
}

enum class EndpointEvent {
    /** Still listening. */
    LISTENING,

    /** Speech, then the silence: send the recording. */
    SEND,

    /** No speech for [Endpointing.nothingHeardMs]: drop the recording, send nothing. */
    NOTHING_HEARD,
}

/** What the endpointer says after the audio so far. */
data class Endpoint(
    val event: EndpointEvent = EndpointEvent.LISTENING,
    /** Milliseconds until the recording is sent by itself; null while no countdown is shown. */
    val countdownMs: Long? = null,
    /** A sound too short to be a question (a cough, a click) was heard and set aside. */
    val shortSound: Boolean = false,
)

/**
 * Finds the end of speech in a recording of 16 kHz mono 16-bit little-endian PCM, from its
 * loudness against a noise floor (H1). Feed it the bytes as the recorder reads them, in any
 * lengths. Once it has said [EndpointEvent.SEND] or [EndpointEvent.NOTHING_HEARD] it keeps
 * saying so. Not thread-safe: one thread feeds it; [heardSpeech] may be read from another.
 */
class Endpointer(private val config: Endpointing = Endpointing()) {
    private val frameSamples = Wav.SAMPLE_RATE * FRAME_MS.toInt() / 1_000
    private val calibrationFrames = (config.calibrationMs / FRAME_MS).toInt().coerceAtLeast(1)

    private var sumSquares = 0.0
    private var samplesInFrame = 0
    // The low byte of a sample whose high byte is in the next read; -1 when there is none.
    private var lowByte = -1

    private val calibration = ArrayList<Double>(calibrationFrames)
    private var calibrated = false
    private var floorRms = config.minFloor

    private var frames = 0L
    private var utteranceSpeechMs = 0L
    private var silenceRunMs = 0L
    private var lastSoundFrame = 0L
    private var lastSpeechFrame = 0L
    private var qualified = false
    private var shortSound = false
    private var event = EndpointEvent.LISTENING

    /** True once an utterance has had enough speech to be sent. */
    @Volatile
    var heardSpeech = false
        private set

    /** The current noise floor (RMS); for tests. */
    val noiseFloor: Double get() = floorRms

    fun feed(pcm: ByteArray, offset: Int = 0, length: Int = pcm.size - offset): Endpoint {
        require(offset >= 0 && length >= 0 && offset + length <= pcm.size) { "outside the buffer" }
        var i = offset
        val end = offset + length
        if (lowByte >= 0 && i < end) {
            sample((pcm[i].toInt() shl 8) or lowByte)
            lowByte = -1
            i++
        }
        while (i + 1 < end) {
            sample((pcm[i + 1].toInt() shl 8) or (pcm[i].toInt() and 0xFF))
            i += 2
        }
        if (i < end) lowByte = pcm[i].toInt() and 0xFF
        return current()
    }

    /** The bytes of a recording of [recorded] bytes to send: up to [Endpointing.tailMs] after the last speech (H9). */
    fun sendLength(recorded: Int): Int {
        val keepMs = lastSpeechFrame * FRAME_MS + config.tailMs
        return minOf(recorded.toLong(), keepMs * Wav.BYTES_PER_SECOND / 1_000).toInt()
    }

    private fun sample(value: Int) {
        sumSquares += value.toDouble() * value
        samplesInFrame++
        if (samplesInFrame == frameSamples) {
            frame(sqrt(sumSquares / frameSamples))
            sumSquares = 0.0
            samplesInFrame = 0
        }
    }

    private fun frame(rms: Double) {
        if (event != EndpointEvent.LISTENING) return
        if (calibrated) {
            classify(rms)
            return
        }
        calibration += rms
        if (calibration.size < calibrationFrames) return
        // The lower quartile: the quiet part of the start, the room rather than a first word.
        val sorted = calibration.sorted()
        floorRms = sorted[(sorted.size - 1) / 4].coerceIn(config.minFloor, maxOf(config.minFloor, config.maxInitialFloor))
        calibrated = true
        // The start is classified now that the floor is known, so a word at the tap counts.
        calibration.forEach(::classify)
        calibration.clear()
    }

    private fun classify(rms: Double) {
        if (event != EndpointEvent.LISTENING) return
        frames++
        if (rms >= floorRms * config.speechRatio) {
            utteranceSpeechMs += FRAME_MS
            silenceRunMs = 0
            lastSoundFrame = frames
            lastSpeechFrame = frames
            if (!qualified && utteranceSpeechMs >= config.minSpeechMs) {
                qualified = true
                shortSound = false
                heardSpeech = true
            }
        } else {
            silenceRunMs += FRAME_MS
            // The floor follows the room: quickly down, slowly up, on frames that are not speech.
            val step = if (rms < floorRms) FLOOR_DOWN else FLOOR_UP
            floorRms = maxOf(config.minFloor, floorRms + (rms - floorRms) * step)
            if (!qualified && utteranceSpeechMs > 0 && silenceRunMs >= config.silenceMs) {
                // Too short to be a question: set aside, it never adds to later speech (H4).
                utteranceSpeechMs = 0
                shortSound = true
            }
        }
        event = when {
            qualified && silenceRunMs >= config.silenceMs -> EndpointEvent.SEND
            !qualified && (frames - lastSoundFrame) * FRAME_MS >= config.nothingHeardMs -> EndpointEvent.NOTHING_HEARD
            else -> EndpointEvent.LISTENING
        }
    }

    private fun current(): Endpoint {
        val counting = event == EndpointEvent.LISTENING && qualified && silenceRunMs >= config.countdownAfterMs
        return Endpoint(event, if (counting) config.silenceMs - silenceRunMs else null, shortSound)
    }

    companion object {
        const val FRAME_MS = 20L
        private const val FLOOR_DOWN = 0.3
        private const val FLOOR_UP = 0.02
    }
}

enum class SilenceAction { NONE, SEND, DROP }

/** What the app does with the endpointer's verdict (H4, H7), and the words it shows. */
object SilenceSend {
    const val SHORT_SOUND = "Only a short sound so far. Still listening."
    const val NOTHING_HEARD = "Nothing heard, so nothing was sent."
    const val SHORT_SOUND_DROPPED = "Only a short sound, so nothing was sent."

    /**
     * Sending by itself is only for a recording started with a tap, with the setting on: while
     * the button is held, letting go sends (H7).
     */
    fun action(enabled: Boolean, phase: Phase, holding: Boolean, endpoint: Endpoint?): SilenceAction {
        if (!enabled || phase != Phase.RECORDING || holding || endpoint == null) return SilenceAction.NONE
        return when (endpoint.event) {
            EndpointEvent.SEND -> SilenceAction.SEND
            EndpointEvent.NOTHING_HEARD -> SilenceAction.DROP
            EndpointEvent.LISTENING -> SilenceAction.NONE
        }
    }

    /** The recorder's two-minute limit: a hands-free recording without speech is dropped, never sent (H4). */
    fun atLimit(handsFree: Boolean, heardSpeech: Boolean): SilenceAction =
        if (handsFree && !heardSpeech) SilenceAction.DROP else SilenceAction.SEND

    /** The note for a dropped recording. */
    fun dropped(endpoint: Endpoint?): String = if (endpoint?.shortSound == true) SHORT_SOUND_DROPPED else NOTHING_HEARD

    /** A yes is expected when a read-back played to its end less than a minute ago (H5). */
    fun yesExpected(readBackPlayedAtMs: Long?, nowMs: Long): Boolean =
        readBackPlayedAtMs != null && nowMs - readBackPlayedAtMs in 0 until ReadBack.PENDING_MS
}
