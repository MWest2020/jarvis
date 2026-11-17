package io.github.mwest2020.jarvis.core

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** 16-bit PCM in a WAV container: what the bridge's faster-whisper decodes without guessing. */
object Wav {
    const val SAMPLE_RATE = 16_000
    const val CHANNELS = 1
    const val BYTES_PER_SAMPLE = 2
    const val BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE
    const val HEADER_SIZE = 44

    fun encode(pcm: ByteArray, sampleRate: Int = SAMPLE_RATE, channels: Int = CHANNELS): ByteArray {
        val blockAlign = channels * BYTES_PER_SAMPLE
        val out = ByteBuffer.allocate(HEADER_SIZE + pcm.size).order(ByteOrder.LITTLE_ENDIAN)
        out.put("RIFF".toByteArray(Charsets.US_ASCII))
        out.putInt(36 + pcm.size)
        out.put("WAVE".toByteArray(Charsets.US_ASCII))
        out.put("fmt ".toByteArray(Charsets.US_ASCII))
        out.putInt(16)
        out.putShort(1) // PCM
        out.putShort(channels.toShort())
        out.putInt(sampleRate)
        out.putInt(sampleRate * blockAlign)
        out.putShort(blockAlign.toShort())
        out.putShort((BYTES_PER_SAMPLE * 8).toShort())
        out.put("data".toByteArray(Charsets.US_ASCII))
        out.putInt(pcm.size)
        out.put(pcm)
        return out.array()
    }

    fun durationMs(pcmBytes: Int): Long = pcmBytes * 1000L / BYTES_PER_SECOND
}
