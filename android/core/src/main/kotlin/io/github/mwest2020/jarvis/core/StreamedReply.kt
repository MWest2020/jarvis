package io.github.mwest2020.jarvis.core

import java.io.Closeable
import java.io.IOException
import java.io.InputStream
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The streamed reply's format (change jarvis-streaming-speech, S1 and S2): 16-bit linear PCM,
 * mono, big-endian as RFC 2586 defines `audio/L16`, in a chunked body that the bridge sends
 * while Kokoro still renders the next sentence.
 */
object StreamFormat {
    const val SAMPLE_RATE = 24_000

    /** What the app asks for: the stream first, the WAV of an older bridge as the fallback. */
    const val ACCEPT = "audio/L16;rate=24000;channels=1, audio/wav;q=0.5"

    /** Playback starts once this much audio is buffered, or at the end of a shorter reply. */
    const val PREROLL_MS = 300

    /** The player's buffer: room for the pre-roll and one read on top. */
    const val TRACK_BUFFER_MS = 1_000

    fun prerollSamples(sampleRate: Int): Int = sampleRate * PREROLL_MS / 1_000

    /** True when [contentType] is `audio/L16`, whatever its parameters. */
    fun isStream(contentType: String?): Boolean =
        contentType?.substringBefore(';')?.trim()?.equals("audio/L16", ignoreCase = true) == true

    /**
     * The sample rate of an `audio/L16` reply this app can play: mono (no `channels`, or 1),
     * a rate between 8 and 48 kHz. Null for anything else.
     */
    fun sampleRate(contentType: String?): Int? {
        if (!isStream(contentType)) return null
        val params = contentType!!.split(';').drop(1).associate { param ->
            val name = param.substringBefore('=').trim().lowercase()
            val value = param.substringAfter('=', "").trim().trim('"')
            name to value
        }
        if ((params["channels"] ?: "1") != "1") return null
        val rate = params["rate"]?.toIntOrNull() ?: return null
        return rate.takeIf { it in 8_000..48_000 }
    }
}

/**
 * Turns a body of big-endian 16-bit samples into [ShortArray]s, however the bytes are split
 * over reads and chunks: a sample cut in two by a read boundary is put together again.
 *
 * [read] throws an [IOException] when the body breaks off (the bridge cut the stream, or the
 * connection went), when it ends in the middle of a sample, and when it grows past
 * [limitBytes]. A clean end of the stream is -1.
 */
class PcmReader(private val input: InputStream, private val limitBytes: Long = BridgeClient.REPLY_LIMIT.toLong()) {
    private var buffer = ByteArray(0)
    private var carry = -1
    private var total = 0L

    /** Blocks until at least one sample has arrived; the number of samples put into [into], or -1 at the end. */
    fun read(into: ShortArray): Int {
        require(into.isNotEmpty()) { "an empty array cannot take a sample" }
        val wanted = into.size * 2
        if (buffer.size < wanted) buffer = ByteArray(wanted)
        while (true) {
            var offset = 0
            if (carry >= 0) {
                buffer[0] = carry.toByte()
                offset = 1
            }
            val n = input.read(buffer, offset, wanted - offset)
            if (n < 0) {
                if (carry >= 0) throw IOException("The stream ended in the middle of a sample.")
                return -1
            }
            total += n
            if (total > limitBytes) throw IOException("The streamed reply is longer than the app accepts.")
            val available = offset + n
            val samples = available / 2
            for (i in 0 until samples) {
                into[i] = ((buffer[2 * i].toInt() shl 8) or (buffer[2 * i + 1].toInt() and 0xFF)).toShort()
            }
            carry = if (available % 2 == 1) buffer[available - 1].toInt() and 0xFF else -1
            if (samples > 0) return samples
        }
    }
}

/**
 * A reply whose headers have arrived and whose audio is still coming. The connection stays
 * open until [close], which the player calls when the reply has played, broke off, or was
 * stopped; closing from another thread makes a blocked [read] fail.
 */
class StreamedReply(
    val summary: String,
    val sessionId: String?,
    val projectsChanged: Boolean,
    val needsConfirmation: Boolean,
    /** The read-back's id (`X-Jarvis-Confirmation-Id`), for [ReadBack]; null on other replies. */
    val confirmationId: String?,
    val sampleRate: Int,
    private val reader: PcmReader,
    private val onClose: () -> Unit,
) : Closeable {
    private val closed = AtomicBoolean(false)

    fun read(into: ShortArray): Int = reader.read(into)

    override fun close() {
        if (closed.compareAndSet(false, true)) onClose()
    }
}

/** How playing a reply ended. [STOPPED]: Mark tapped, or the app closed. */
enum class PlaybackEnd { PLAYED, STOPPED, BROKE_OFF, FAILED }

object Playback {
    /** What the screen says after playback, next to the summary; null when it played. */
    fun message(end: PlaybackEnd): String? = when (end) {
        PlaybackEnd.PLAYED, PlaybackEnd.STOPPED -> null
        PlaybackEnd.BROKE_OFF -> "The spoken reply broke off. Its text is above."
        PlaybackEnd.FAILED -> "The reply could not be played."
    }
}

/**
 * A read-back that was not played to its end was not heard, so it may not stay pending
 * (change jarvis-streaming-speech, S6): the app sends `POST /turn/cancel` and the bridge
 * answers `no` to the agent host. Without it, the bridge has kept the read-back as soon as
 * its last byte was written, which is before the phone has played it.
 */
object ReadBack {
    const val CANCELLED = "Cancelled. Nothing was confirmed."
    const val NOT_CANCELLED =
        "The read-back was stopped, but the bridge could not be told. Do not say yes; it expires within a minute."

    /** True when a reply that asked for a spoken yes ended any other way than played to its end. */
    fun mustCancel(needsConfirmation: Boolean, confirmationId: String?, end: PlaybackEnd): Boolean =
        needsConfirmation && !confirmationId.isNullOrEmpty() && end != PlaybackEnd.PLAYED

    /** The same id shape the bridge accepts; anything else is never sent. */
    fun isValidId(id: String): Boolean = id.length in 1..64 && id.all { it.isLetterOrDigit() && it.code < 128 || it == '_' || it == '-' }
}

/** The answer to `POST /turn/cancel`. */
sealed interface CancelOutcome {
    /** [cancelled]: a pending read-back was cancelled now; false when nothing with that id was pending. */
    data class Done(val cancelled: Boolean) : CancelOutcome
    data class Failed(val failure: TurnFailure) : CancelOutcome
}
