package io.github.mwest2020.jarvis

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaDataSource
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.media.ToneGenerator
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import io.github.mwest2020.jarvis.core.PlaybackEnd
import io.github.mwest2020.jarvis.core.StreamFormat
import io.github.mwest2020.jarvis.core.StreamedReply
import io.github.mwest2020.jarvis.core.Wav
import java.io.ByteArrayOutputStream
import java.io.IOException
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

/**
 * Plays a streamed reply while it arrives (change jarvis-streaming-speech, S7): an
 * [AudioTrack] in [AudioTrack.MODE_STREAM] with a one-second buffer, started once
 * [StreamFormat.PREROLL_MS] of audio is in it (the jitter buffer), or at the end of a shorter
 * reply. One worker thread reads the connection and writes the track; nothing is stored.
 */
class StreamPlayer {
    private val main = Handler(Looper.getMainLooper())

    // Main thread only.
    private var current: Session? = null

    private class Session(val reply: StreamedReply) {
        @Volatile
        var stopped = false

        @Volatile
        var track: AudioTrack? = null
    }

    /** [onDone] runs on the main thread when the reply has played, broke off or failed; never after [stop]. */
    fun play(reply: StreamedReply, onDone: (PlaybackEnd) -> Unit) {
        stop()
        val session = Session(reply)
        current = session
        thread(name = "jarvis-reply") {
            val end = try {
                playAll(session)
            } finally {
                session.reply.close()
            }
            main.post {
                if (current === session && !session.stopped) {
                    current = null
                    onDone(end)
                }
            }
        }
    }

    /** Stops at once: the track is paused and emptied, the connection closed. */
    fun stop() {
        val session = current ?: return
        current = null
        session.stopped = true
        session.track?.let { track ->
            try {
                track.pause()
                track.flush()
            } catch (_: IllegalStateException) {
                // Already released by the worker.
            }
        }
        // Closing the connection unblocks a read; the network is not touched on the main thread.
        thread(name = "jarvis-reply-close") { session.reply.close() }
    }

    private fun playAll(session: Session): PlaybackEnd {
        val rate = session.reply.sampleRate
        val minBuffer = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuffer <= 0) return PlaybackEnd.FAILED
        val track = try {
            AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(rate)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build(),
                )
                .setTransferMode(AudioTrack.MODE_STREAM)
                .setBufferSizeInBytes(maxOf(minBuffer, rate * Wav.BYTES_PER_SAMPLE * StreamFormat.TRACK_BUFFER_MS / 1_000))
                .build()
        } catch (_: IllegalArgumentException) {
            return PlaybackEnd.FAILED
        } catch (_: UnsupportedOperationException) {
            return PlaybackEnd.FAILED
        }
        if (track.state != AudioTrack.STATE_INITIALIZED) {
            track.release()
            return PlaybackEnd.FAILED
        }
        session.track = track
        try {
            return stream(session, track, rate)
        } catch (_: IllegalStateException) {
            return if (session.stopped) PlaybackEnd.PLAYED else PlaybackEnd.FAILED
        } finally {
            session.track = null
            try {
                track.stop()
            } catch (_: IllegalStateException) {
                // Never started.
            }
            track.release()
        }
    }

    private fun stream(session: Session, track: AudioTrack, rate: Int): PlaybackEnd {
        val preroll = StreamFormat.prerollSamples(rate)
        // 2048 samples (85 ms) per read: with the pre-roll this stays well inside the buffer
        // before playback starts, so a blocking write cannot wait on a track that is not playing.
        val samples = ShortArray(READ_SAMPLES)
        var written = 0L
        var started = false
        var end = PlaybackEnd.PLAYED
        while (!session.stopped) {
            val n = try {
                session.reply.read(samples)
            } catch (_: IOException) {
                // The bridge cut the stream, or the connection went: play what arrived, then say so.
                if (!session.stopped) end = PlaybackEnd.BROKE_OFF
                break
            }
            if (n < 0) break
            var offset = 0
            while (offset < n && !session.stopped) {
                val count = track.write(samples, offset, n - offset)
                if (count <= 0) return if (session.stopped) PlaybackEnd.PLAYED else PlaybackEnd.FAILED
                offset += count
            }
            written += n
            if (!started && written >= preroll && !session.stopped) {
                track.play()
                started = true
            }
        }
        if (session.stopped || written == 0L) return end
        if (!started) track.play()
        // In streaming mode stop() plays out what is buffered; then wait until it has.
        track.stop()
        val deadline = SystemClock.elapsedRealtime() + written * 1_000 / rate + DRAIN_SLACK_MS
        while (!session.stopped && SystemClock.elapsedRealtime() < deadline) {
            if ((track.playbackHeadPosition.toLong() and 0xFFFFFFFFL) >= written) break
            SystemClock.sleep(POLL_MS)
        }
        return end
    }

    private companion object {
        const val READ_SAMPLES = 2_048
        const val DRAIN_SLACK_MS = 1_500L
        const val POLL_MS = 20L
    }
}
