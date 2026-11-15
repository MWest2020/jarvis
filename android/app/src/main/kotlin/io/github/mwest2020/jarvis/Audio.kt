package io.github.mwest2020.jarvis

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaDataSource
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.media.ToneGenerator
import io.github.mwest2020.jarvis.core.Wav
import java.io.ByteArrayOutputStream
import kotlin.concurrent.thread

/**
 * Records 16 kHz mono 16-bit PCM into memory. Raw PCM in a WAV header is the one format the
 * bridge decodes without guessing (faster-whisper reads it through PyAV, `bridge/stt.py`).
 * A recording stops by itself at [MAX_SECONDS], far below the bridge's 10 MB limit.
 */
class Recorder(private val onLimitReached: () -> Unit) {
    @Volatile
    private var recording = false
    private var record: AudioRecord? = null
    private var worker: Thread? = null
    private val pcm = ByteArrayOutputStream()

    /** The caller has checked the RECORD_AUDIO permission. False when the microphone is not available. */
    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (recording) return true
        val minBuffer = AudioRecord.getMinBufferSize(
            Wav.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer <= 0) return false
        val created = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                Wav.SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                minBuffer * 4,
            )
        } catch (_: IllegalArgumentException) {
            return false
        } catch (_: SecurityException) {
            return false
        }
        if (created.state != AudioRecord.STATE_INITIALIZED) {
            created.release()
            return false
        }
        try {
            created.startRecording()
        } catch (_: IllegalStateException) {
            created.release()
            return false
        }
        pcm.reset()
        record = created
        recording = true
        worker = thread(name = "jarvis-recorder") {
            val chunk = ByteArray(minBuffer)
            while (recording) {
                val read = created.read(chunk, 0, chunk.size)
                if (read < 0) break
                if (read == 0) continue
                val full = synchronized(pcm) {
                    pcm.write(chunk, 0, read)
                    pcm.size() >= MAX_SECONDS * Wav.BYTES_PER_SECOND
                }
                if (full) {
                    recording = false
                    onLimitReached()
                }
            }
        }
        return true
    }

    /** Stops and returns the PCM recorded so far (empty when nothing was recording). */
    fun stop(): ByteArray {
        val active = record ?: return ByteArray(0)
        recording = false
        worker?.join(1_000)
        worker = null
        record = null
        try {
            active.stop()
        } catch (_: IllegalStateException) {
            // Already stopped.
        }
        active.release()
        return synchronized(pcm) { pcm.toByteArray().also { pcm.reset() } }
    }

    companion object {
        const val MAX_SECONDS = 120
    }
}

/**
 * The acknowledgement while the bridge works: one beep when the recording has gone out, then
 * a soft tick every few seconds, so a wait of 15 to 20 seconds is never silent. Produced on
 * the phone; it needs no connection.
 */
class WaitTone {
    private var generator: ToneGenerator? = null

    private fun generator(): ToneGenerator? {
        generator?.let { return it }
        return try {
            ToneGenerator(AudioManager.STREAM_MUSIC, 45).also { generator = it }
        } catch (_: RuntimeException) {
            null
        }
    }

    fun heard() {
        generator()?.startTone(ToneGenerator.TONE_PROP_ACK, 200)
    }

    fun tick() {
        generator()?.startTone(ToneGenerator.TONE_PROP_BEEP, 60)
    }

    fun problem() {
        generator()?.startTone(ToneGenerator.TONE_PROP_NACK, 300)
    }

    fun release() {
        generator?.release()
        generator = null
    }
}

/**
 * Plays the bridge's WAV reply straight from memory: nothing is written to storage, and
 * preparing happens off the main thread (`prepareAsync`).
 */
class ReplyPlayer {
    private var player: MediaPlayer? = null

    /** [onDone] runs on the main thread: true after the reply played, false when it could not. */
    fun play(wav: ByteArray, onDone: (played: Boolean) -> Unit) {
        stop()
        val created = MediaPlayer()
        try {
            created.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            created.setDataSource(InMemorySource(wav))
            created.setOnPreparedListener { it.start() }
            created.setOnCompletionListener {
                stop()
                onDone(true)
            }
            created.setOnErrorListener { _, _, _ ->
                stop()
                onDone(false)
                true
            }
            player = created
            created.prepareAsync()
        } catch (_: IllegalStateException) {
            fail(created, onDone)
        } catch (_: IllegalArgumentException) {
            fail(created, onDone)
        }
    }

    private fun fail(created: MediaPlayer, onDone: (Boolean) -> Unit) {
        player = null
        created.release()
        onDone(false)
    }

    fun stop() {
        player?.let {
            try {
                it.stop()
            } catch (_: IllegalStateException) {
                // Not started.
            }
            it.release()
        }
        player = null
    }

    private class InMemorySource(private val bytes: ByteArray) : MediaDataSource() {
        override fun readAt(position: Long, buffer: ByteArray, offset: Int, size: Int): Int {
            if (position >= bytes.size) return -1
            val count = minOf(size, bytes.size - position.toInt())
            System.arraycopy(bytes, position.toInt(), buffer, offset, count)
            return count
        }

        override fun getSize(): Long = bytes.size.toLong()

        override fun close() = Unit
    }
}
