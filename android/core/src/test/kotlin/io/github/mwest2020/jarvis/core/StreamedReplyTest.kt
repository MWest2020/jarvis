package io.github.mwest2020.jarvis.core

import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.URI
import kotlin.concurrent.thread

/** The streamed reply (jarvis-streaming-speech): its format, the sample reader, and the client against a raw bridge. */
class StreamedReplyTest {
    // --- the format ---------------------------------------------------------------------

    @Test
    fun readsTheBridgesFormat() {
        assertEquals(24_000, StreamFormat.sampleRate("audio/L16;rate=24000;channels=1"))
        assertEquals(24_000, StreamFormat.sampleRate("audio/l16; rate=24000"))
        assertEquals(16_000, StreamFormat.sampleRate("AUDIO/L16;RATE=\"16000\";Channels=1"))
    }

    @Test
    fun refusesWhatItCannotPlay() {
        assertNull(StreamFormat.sampleRate("audio/wav"))
        assertNull(StreamFormat.sampleRate(null))
        assertNull(StreamFormat.sampleRate("audio/L16"))
        assertNull(StreamFormat.sampleRate("audio/L16;rate=24000;channels=2"))
        assertNull(StreamFormat.sampleRate("audio/L16;rate=96000"))
        assertNull(StreamFormat.sampleRate("audio/L16;rate=fast"))
        assertTrue(StreamFormat.isStream("audio/L16;rate=24000;channels=2"))
        assertFalse(StreamFormat.isStream("audio/wav"))
        assertFalse(StreamFormat.isStream("audio/L16x"))
    }

    @Test
    fun theJitterBufferIsAThirdOfASecond() {
        assertEquals(7_200, StreamFormat.prerollSamples(24_000))
        assertTrue(StreamFormat.TRACK_BUFFER_MS > StreamFormat.PREROLL_MS)
    }

    @Test
    fun aBrokenStreamSaysTheTextIsOnScreen() {
        assertNull(Playback.message(PlaybackEnd.PLAYED))
        assertEquals("The spoken reply broke off. Its text is above.", Playback.message(PlaybackEnd.BROKE_OFF))
        assertEquals("The reply could not be played.", Playback.message(PlaybackEnd.FAILED))
    }

    // --- the reader ---------------------------------------------------------------------

    /** Hands out the bytes in the given read sizes, as a network would. */
    private class Trickle(private val bytes: ByteArray, sizes: List<Int>) : InputStream() {
        private val sizes = ArrayDeque(sizes)
        private var position = 0

        override fun read(): Int = if (position < bytes.size) bytes[position++].toInt() and 0xFF else -1

        override fun read(b: ByteArray, off: Int, len: Int): Int {
            if (position >= bytes.size) return -1
            val n = minOf(len, sizes.removeFirstOrNull() ?: len, bytes.size - position)
            System.arraycopy(bytes, position, b, off, n)
            position += n
            return n
        }
    }

    private fun readAll(reader: PcmReader, size: Int = 4): ShortArray {
        val out = mutableListOf<Short>()
        val into = ShortArray(size)
        while (true) {
            val n = reader.read(into)
            if (n < 0) break
            for (i in 0 until n) out += into[i]
        }
        return out.toShortArray()
    }

    private val samples = shortArrayOf(0, 1, -1, 0x1234, Short.MAX_VALUE, Short.MIN_VALUE, 256)
    private val bigEndian = samples.flatMap { listOf((it.toInt() shr 8).toByte(), it.toByte()) }.toByteArray()

    @Test
    fun decodesBigEndianSamples() {
        assertArrayEquals(samples, readAll(PcmReader(ByteArrayInputStream(bigEndian))))
    }

    @Test
    fun putsTogetherASampleSplitAcrossReads() {
        // 1, 3, 1, 1, 5, ... bytes per read: samples are cut in two over and over.
        val reader = PcmReader(Trickle(bigEndian, listOf(1, 3, 1, 1, 5, 2, 1)))
        assertArrayEquals(samples, readAll(reader, size = 3))
    }

    @Test
    fun aStreamThatEndsInTheMiddleOfASampleIsBroken() {
        val reader = PcmReader(ByteArrayInputStream(bigEndian.copyOf(bigEndian.size - 1)))
        try {
            readAll(reader)
            fail("expected an IOException")
        } catch (_: IOException) {
        }
    }

    @Test
    fun aStreamLongerThanTheLimitIsRefused() {
        val reader = PcmReader(ByteArrayInputStream(ByteArray(64)), limitBytes = 32)
        try {
            readAll(reader, size = 8)
            fail("expected an IOException")
        } catch (_: IOException) {
        }
    }

    // --- the client against a raw bridge ------------------------------------------------

    private val servers = mutableListOf<ServerSocket>()
    @Volatile
    private var seenHead = ""

    @After
    fun stop() {
        servers.forEach { it.close() }
    }

    /** One connection: reads the request (head and body), then [respond] writes the raw reply and the socket closes. */
    private fun rawBridge(respond: (OutputStream) -> Unit): String {
        val socket = ServerSocket(0, 1, InetAddress.getLoopbackAddress())
        servers += socket
        thread(isDaemon = true) {
            socket.accept().use { client ->
                val input = client.getInputStream()
                val head = StringBuilder()
                while (!head.endsWith("\r\n\r\n")) {
                    val b = input.read()
                    if (b < 0) break
                    head.append(b.toChar())
                }
                val length = Regex("(?i)content-length:\\s*(\\d+)").find(head)?.groupValues?.get(1)?.toInt() ?: 0
                var left = length
                val buffer = ByteArray(4096)
                while (left > 0) {
                    val n = input.read(buffer, 0, minOf(buffer.size, left))
                    if (n < 0) break
                    left -= n
                }
                seenHead = head.toString()
                val out = client.getOutputStream()
                respond(out)
                out.flush()
            }
        }
        return "http://127.0.0.1:${socket.localPort}"
    }

    private fun streamHead(extra: String = "") =
        ("HTTP/1.1 200 OK\r\nContent-Type: audio/L16;rate=24000;channels=1\r\nTransfer-Encoding: chunked\r\n" +
            "Connection: close\r\nX-Jarvis-Summary: The%20answer%20is%20four.\r\nX-Jarvis-Session-Id: s-1\r\n" +
            extra + "\r\n").toByteArray(Charsets.US_ASCII)

    private fun chunk(bytes: ByteArray): ByteArray =
        "%x\r\n".format(bytes.size).toByteArray(Charsets.US_ASCII) + bytes + "\r\n".toByteArray(Charsets.US_ASCII)

    private val end = "0\r\n\r\n".toByteArray(Charsets.US_ASCII)

    private fun built(base: String): TurnRequest {
        val ok = TurnRequests.build("http://jarvis.example.ts.net:8787", "s3cret-token", "demo") as Built.Ok
        return TurnRequest(BridgeUrl.turnUrl(URI(base), "demo"), ok.value.headers)
    }

    private val client = BridgeClient(requireTailnetForCleartext = false, readTimeoutMs = 5_000)

    @Test
    fun playsAStreamWhoseChunksCutSamplesInTwo() {
        val base = rawBridge { out ->
            out.write(
                streamHead(
                    "X-Jarvis-Projects-Changed: 1\r\nX-Jarvis-Needs-Confirmation: 1\r\nX-Jarvis-Confirmation-Id: c-42\r\n",
                ),
            )
            // Chunks of 3, 1 and the rest: two samples are cut by a chunk boundary.
            out.write(chunk(bigEndian.copyOfRange(0, 3)))
            out.write(chunk(bigEndian.copyOfRange(3, 4)))
            out.write(chunk(bigEndian.copyOfRange(4, bigEndian.size)))
            out.write(end)
        }
        val outcome = client.turn(built(base), ByteArray(100)) as TurnOutcome.Streamed
        val reply = outcome.reply
        assertTrue(seenHead.contains("Accept: audio/L16;rate=24000;channels=1, audio/wav;q=0.5"))
        assertEquals("The answer is four.", reply.summary)
        assertEquals("s-1", reply.sessionId)
        assertEquals(24_000, reply.sampleRate)
        assertTrue(reply.projectsChanged)
        assertTrue(reply.needsConfirmation)
        assertEquals("c-42", reply.confirmationId)
        val got = mutableListOf<Short>()
        val into = ShortArray(2)
        while (true) {
            val n = reply.read(into)
            if (n < 0) break
            for (i in 0 until n) got += into[i]
        }
        reply.close()
        reply.close() // a second close does nothing
        assertArrayEquals(samples, got.toShortArray())
    }

    @Test
    fun aCutStreamFailsAfterTheAudioThatArrived() {
        val base = rawBridge { out ->
            out.write(streamHead())
            out.write(chunk(bigEndian.copyOfRange(0, 4)))
            // No terminating chunk: the bridge cut the stream and closed.
        }
        val reply = (client.turn(built(base), ByteArray(100)) as TurnOutcome.Streamed).reply
        assertFalse(reply.needsConfirmation)
        assertNull(reply.confirmationId)
        val into = ShortArray(16)
        val got = mutableListOf<Short>()
        try {
            while (true) {
                val n = reply.read(into)
                if (n < 0) fail("a cut stream must not end cleanly")
                for (i in 0 until n) got += into[i]
            }
        } catch (_: IOException) {
        } finally {
            reply.close()
        }
        assertArrayEquals(samples.copyOfRange(0, 2), got.toShortArray())
    }

    @Test
    fun aStreamWithoutAudioEndsCleanly() {
        val base = rawBridge { out ->
            out.write(streamHead())
            out.write(end)
        }
        val reply = (client.turn(built(base), ByteArray(100)) as TurnOutcome.Streamed).reply
        assertEquals(-1, reply.read(ShortArray(4)))
        reply.close()
    }

    @Test
    fun anOlderBridgesWavIsPlayedAsBefore() {
        val wav = Wav.encode(ByteArray(640))
        val base = rawBridge { out ->
            out.write(
                ("HTTP/1.0 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: ${wav.size}\r\n" +
                    "X-Jarvis-Summary: Four.\r\nX-Jarvis-Needs-Confirmation: 1\r\nX-Jarvis-Confirmation-Id: c-7\r\n\r\n")
                    .toByteArray(Charsets.US_ASCII),
            )
            out.write(wav)
        }
        val reply = client.turn(built(base), ByteArray(100)) as TurnOutcome.Reply
        assertArrayEquals(wav, reply.wav)
        assertEquals("Four.", reply.summary)
        assertTrue(reply.needsConfirmation)
        assertEquals("c-7", reply.confirmationId)
    }

    @Test
    fun aStreamInAFormatTheAppCannotPlayIsABadReply() {
        val base = rawBridge { out ->
            out.write(
                "HTTP/1.1 200 OK\r\nContent-Type: audio/L16;rate=24000;channels=2\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
                    .toByteArray(Charsets.US_ASCII),
            )
        }
        val outcome = client.turn(built(base), ByteArray(100)) as TurnOutcome.Failed
        assertEquals(FailureKind.BAD_REPLY, outcome.failure.kind)
    }

    // --- cancelling a read-back that was not played to its end (S6) --------------------

    @Test
    fun aReadBackIsCancelledUnlessItPlayedToItsEnd() {
        assertFalse(ReadBack.mustCancel(true, "c-1", PlaybackEnd.PLAYED))
        assertTrue(ReadBack.mustCancel(true, "c-1", PlaybackEnd.STOPPED))
        assertTrue(ReadBack.mustCancel(true, "c-1", PlaybackEnd.BROKE_OFF))
        assertTrue(ReadBack.mustCancel(true, "c-1", PlaybackEnd.FAILED))
        // Not a read-back, or a bridge that sends no id: nothing to cancel.
        assertFalse(ReadBack.mustCancel(false, "c-1", PlaybackEnd.STOPPED))
        assertFalse(ReadBack.mustCancel(true, null, PlaybackEnd.STOPPED))
        assertFalse(ReadBack.mustCancel(true, "", PlaybackEnd.BROKE_OFF))
    }

    @Test
    fun onlyAnIdOfTheBridgesShapeIsSent() {
        assertTrue(ReadBack.isValidId("a1_B-2"))
        assertFalse(ReadBack.isValidId(""))
        assertFalse(ReadBack.isValidId("x".repeat(65)))
        assertFalse(ReadBack.isValidId("a b"))
        assertFalse(ReadBack.isValidId("a&id=b"))
        assertFalse(ReadBack.isValidId("é"))
        val refused = TurnRequests.cancel("http://jarvis.example.ts.net:8787", "s3cret-token", "demo", "a&b")
        assertTrue(refused is Built.Refused)
    }

    @Test
    fun theCancelRequestNamesTheProjectAndTheId() {
        val built = TurnRequests.cancel("http://jarvis.example.ts.net:8787", "s3cret-token", "my app", "c-42") as Built.Ok
        assertEquals("http://jarvis.example.ts.net:8787/turn/cancel?project=my%20app&id=c-42", built.value.url.toString())
        assertEquals(mapOf("Authorization" to "Bearer s3cret-token"), built.value.headers)
        assertFalse(built.value.toString().contains("s3cret-token"))
    }

    private fun cancelRequest(base: String): TurnRequest {
        val ok = TurnRequests.cancel("http://jarvis.example.ts.net:8787", "s3cret-token", "demo", "c-42") as Built.Ok
        return TurnRequest(BridgeUrl.cancelUrl(URI(base), "demo", "c-42"), ok.value.headers)
    }

    private fun jsonReply(status: String, body: String) = (
        "HTTP/1.0 $status\r\nContent-Type: application/json\r\nContent-Length: ${body.length}\r\n\r\n$body"
        ).toByteArray(Charsets.US_ASCII)

    @Test
    fun theBridgeSaysWhetherItCancelled() {
        var base = rawBridge { it.write(jsonReply("200 OK", "{\"cancelled\": true}")) }
        assertEquals(CancelOutcome.Done(true), client.cancel(cancelRequest(base)))
        assertTrue(seenHead.startsWith("POST /turn/cancel?project=demo&id=c-42 HTTP/1.1"))
        assertTrue(seenHead.contains("Authorization: Bearer s3cret-token"))
        base = rawBridge { it.write(jsonReply("200 OK", "{\"cancelled\": false}")) }
        assertEquals(CancelOutcome.Done(false), client.cancel(cancelRequest(base)))
    }

    @Test
    fun aRefusedCancelIsAFailure() {
        val base = rawBridge { it.write(jsonReply("401 Unauthorized", "no")) }
        val outcome = client.cancel(cancelRequest(base)) as CancelOutcome.Failed
        assertEquals(FailureKind.UNAUTHORIZED, outcome.failure.kind)
    }
}
