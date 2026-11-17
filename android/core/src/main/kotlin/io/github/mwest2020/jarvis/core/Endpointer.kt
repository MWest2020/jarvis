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
    /** The start of the recording that gives the noise floor (H1), not counting digital silence. */
    val calibrationMs: Long = 300,
    /** The floor is taken by then at the latest, however much of the start was digital silence. */
    val maxCalibrationMs: Long = 1_000,
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
    private val maxCalibrationFrames = maxOf(calibrationFrames, (config.maxCalibrationMs / FRAME_MS).toInt())

    private var sumSquares = 0.0
    private var samplesInFrame = 0
    // The low byte of a sample whose high byte is in the next read; -1 when there is none.
    private var lowByte = -1

    private val calibration = ArrayList<Double>(maxCalibrationFrames)
    private var sounding = 0
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

    /**
     * True once a pause of at least [Endpointing.countdownAfterMs] followed speech that counted:
     * the endpointer can tell speech from the room. In a room louder than three times the
     * floor every frame is speech and this stays false (H4).
     */
    @Volatile
    var heardPause = false
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
        // Many phones give zeros for the first moment of a recording: that is not the room.
        if (rms >= DIGITAL_SILENCE) sounding++
        if (sounding < calibrationFrames && calibration.size < maxCalibrationFrames) return
        // The lower quartile: the quiet part of the start, the room rather than a first word.
        val sorted = calibration.filter { it >= DIGITAL_SILENCE }.sorted()
        val quartile = if (sorted.isEmpty()) config.minFloor else sorted[(sorted.size - 1) / 4]
        floorRms = quartile.coerceIn(config.minFloor, maxOf(config.minFloor, config.maxInitialFloor))
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
            if (qualified && silenceRunMs >= config.countdownAfterMs) heardPause = true
            // The floor follows the room: quickly down, slowly up, on frames that are not speech.
            // Zeros (the start on some phones, a dropout) say nothing about the room.
            if (rms >= DIGITAL_SILENCE) {
                val step = if (rms < floorRms) FLOOR_DOWN else FLOOR_UP
                floorRms = maxOf(config.minFloor, floorRms + (rms - floorRms) * step)
            }
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
        // Below one step of a 16-bit sample: zeros, not a microphone.
        private const val DIGITAL_SILENCE = 1.0
    }
}

enum class SilenceAction { NONE, SEND, DROP }

/** What the app does with the endpointer's verdict (H4, H7), and the words it shows. */
object SilenceSend {
    const val SHORT_SOUND = "Only a short sound so far. Still listening."
    const val NOTHING_HEARD = "Nothing heard, so nothing was sent."
    const val SHORT_SOUND_DROPPED = "Only a short sound, so nothing was sent."
    const val TOO_NOISY =
        "Too noisy to tell when you stopped, so nothing was sent. Hold the button while you talk, " +
            "or tap again when you are done."

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

    /**
     * The recorder's two-minute limit. A hands-free recording is sent only when it had speech
     * and a pause after it; without speech, or when "speech" never paused (a room so loud that
     * every frame counts), it is dropped, never sent (H4). Held, or with the setting off, the
     * limit sends as before.
     */
    fun atLimit(handsFree: Boolean, heardSpeech: Boolean, heardPause: Boolean): SilenceAction =
        if (handsFree && !(heardSpeech && heardPause)) SilenceAction.DROP else SilenceAction.SEND

    /** The note for a hands-free recording dropped at the limit. */
    fun droppedAtLimit(heardSpeech: Boolean, endpoint: Endpoint?): String =
        if (heardSpeech) TOO_NOISY else dropped(endpoint)

    /** The note for a dropped recording. */
    fun dropped(endpoint: Endpoint?): String = if (endpoint?.shortSound == true) SHORT_SOUND_DROPPED else NOTHING_HEARD

    /**
     * A yes is expected, and the shorter minimum applies (H5), only when a read-back played to
     * its end less than a minute ago and no read-back cancel is still due: a cancel that has not
     * reached the bridge must never be followed by a recording that sends on less speech.
     */
    fun yesExpected(readBackPlayedAtMs: Long?, nowMs: Long, cancels: ReadBackCancels): Boolean =
        readBackPlayedAtMs != null &&
            nowMs - readBackPlayedAtMs in 0 until ReadBack.PENDING_MS &&
            cancels.due(nowMs) == null
}
