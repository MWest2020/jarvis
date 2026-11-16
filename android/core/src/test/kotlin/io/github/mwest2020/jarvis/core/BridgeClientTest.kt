package io.github.mwest2020.jarvis.core

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.ProxySelector
import java.net.SocketAddress
import java.net.URI
import java.net.UnknownHostException

/** The client against a real HTTP server on loopback that behaves like the bridge. */
class BridgeClientTest {
    private class Seen(val method: String, val uri: String, val authorization: String?, val contentType: String?, val body: ByteArray)

    private var server: HttpServer? = null
    private var seen: Seen? = null
    private val wavReply = Wav.encode(ByteArray(640))

    private fun serve(handler: (HttpExchange) -> Unit): String {
        val started = HttpServer.create(InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0)
        started.createContext("/") { exchange ->
            seen = Seen(
                exchange.requestMethod,
                exchange.requestURI.toString(),
                exchange.requestHeaders.getFirst("Authorization"),
                exchange.requestHeaders.getFirst("Content-Type"),
                exchange.requestBody.readBytes(),
            )
            handler(exchange)
            exchange.close()
        }
        started.start()
        server = started
        return "http://127.0.0.1:${started.address.port}"
    }

    private fun respond(exchange: HttpExchange, status: Int, body: ByteArray, headers: Map<String, String> = emptyMap()) {
        headers.forEach { (name, value) -> exchange.responseHeaders.add(name, value) }
        exchange.sendResponseHeaders(status, body.size.toLong())
        exchange.responseBody.use { it.write(body) }
    }

    /** Loopback is not a `*.ts.net` name, so the tests build the request past that rule. */
    private fun request(base: String, token: String = "s3cret-token") = TurnRequest(
        BridgeUrl.turnUrl(URI(base), "demo"),
        mapOf("Authorization" to "Bearer $token", "Content-Type" to "audio/wav"),
    )

    private val loopbackClient = BridgeClient(requireTailnetForCleartext = false, readTimeoutMs = 5_000)

    @After
    fun stop() {
        server?.stop(0)
    }

    @Test
    fun postsTheRecordingAndReturnsTheReply() {
        val base = serve { exchange ->
            respond(
                exchange, 200, wavReply,
                mapOf(
                    "Content-Type" to "audio/wav",
                    "X-Jarvis-Summary" to "It%20uses%20faster-whisper%20and%20Kokoro.",
                    "X-Jarvis-Session-Id" to "abc-123",
                ),
            )
        }
        val recording = Wav.encode(ByteArray(3_200) { 7 })

        val outcome = loopbackClient.turn(request(base), recording) as TurnOutcome.Reply

        assertEquals("POST", seen!!.method)
        assertEquals("/turn?project=demo", seen!!.uri)
        assertEquals("Bearer s3cret-token", seen!!.authorization)
        assertEquals("audio/wav", seen!!.contentType)
        assertArrayEquals(recording, seen!!.body)
        assertArrayEquals(wavReply, outcome.wav)
        assertEquals("It uses faster-whisper and Kokoro.", outcome.summary)
        assertEquals("abc-123", outcome.sessionId)
        assertEquals(false, outcome.projectsChanged)
    }

    @Test
    fun aReplyCanSayThatTheProjectsChanged() {
        val base = serve { respond(it, 200, wavReply, mapOf("X-Jarvis-Projects-Changed" to "1")) }
        val outcome = loopbackClient.turn(request(base), ByteArray(100)) as TurnOutcome.Reply
        assertEquals(true, outcome.projectsChanged)
    }

    @Test
    fun aReplyWithoutSummaryStillPlays() {
        val base = serve { respond(it, 200, wavReply) }
        val outcome = loopbackClient.turn(request(base), ByteArray(100)) as TurnOutcome.Reply
        assertEquals("", outcome.summary)
        assertNull(outcome.sessionId)
    }

    @Test
    fun mapsTheBridgesErrors() {
        val cases = mapOf(
            401 to FailureKind.UNAUTHORIZED,
            409 to FailureKind.BUSY,
            413 to FailureKind.TOO_LARGE,
            502 to FailureKind.AGENT_FAILED,
            500 to FailureKind.SERVER_ERROR,
        )
        for ((status, kind) in cases) {
            val base = serve { respond(it, status, "an error".toByteArray()) }
            val outcome = loopbackClient.turn(request(base), ByteArray(100)) as TurnOutcome.Failed
            assertEquals(kind, outcome.failure.kind)
            server?.stop(0)
        }
    }

    @Test
    fun showsWhyTheBridgeRefusedABadRequest() {
        val base = serve { respond(it, 400, "unknown project: demo".toByteArray()) }
        val outcome = loopbackClient.turn(request(base), ByteArray(100)) as TurnOutcome.Failed
        assertEquals(FailureKind.BAD_REQUEST, outcome.failure.kind)
        assertTrue(outcome.failure.message.endsWith("unknown project: demo"))
    }

    @Test
    fun doesNotFollowARedirect() {
        var hits = 0
        val base = serve { exchange ->
            hits++
            respond(exchange, 302, ByteArray(0), mapOf("Location" to "/elsewhere"))
        }
        val outcome = loopbackClient.turn(request(base), ByteArray(100)) as TurnOutcome.Failed
        assertEquals(FailureKind.SERVER_ERROR, outcome.failure.kind)
        assertEquals(1, hits)
    }

    @Test
    fun refusesAnEmptyReply() {
        val base = serve { respond(it, 200, ByteArray(0)) }
        val outcome = loopbackClient.turn(request(base), ByteArray(100)) as TurnOutcome.Failed
        assertEquals(FailureKind.BAD_REPLY, outcome.failure.kind)
    }

    @Test
    fun aClosedPortReadsAsNoTailnet() {
        val base = serve { respond(it, 200, wavReply) }
        server?.stop(0)
        val outcome = loopbackClient.turn(request(base), ByteArray(100)) as TurnOutcome.Failed
        assertEquals(FailureKind.NO_TAILNET, outcome.failure.kind)
    }

    @Test
    fun sendsNothingOverHttpWhenTheNameIsNotATailscaleAddress() {
        val base = serve { respond(it, 200, wavReply) }
        val client = BridgeClient(resolve = { listOf(InetAddress.getByName("203.0.113.7")) })
        val outcome = client.turn(request(base), ByteArray(100)) as TurnOutcome.Failed
        assertEquals(FailureKind.NOT_A_TAILNET_ADDRESS, outcome.failure.kind)
        assertNull(seen)
    }

    @Test
    fun sendsNothingWhenOneOfSeveralAddressesIsOutsideTheTailnet() {
        val base = serve { respond(it, 200, wavReply) }
        val client = BridgeClient(
            resolve = { listOf(InetAddress.getByName("100.101.102.103"), InetAddress.getByName("192.168.1.10")) },
        )
        val outcome = client.turn(request(base), ByteArray(100)) as TurnOutcome.Failed
        assertEquals(FailureKind.NOT_A_TAILNET_ADDRESS, outcome.failure.kind)
        assertNull(seen)
    }

    @Test
    fun aNameThatDoesNotResolveReadsAsNoTailnet() {
        val base = serve { respond(it, 200, wavReply) }
        val client = BridgeClient(resolve = { throw UnknownHostException(it) })
        val outcome = client.turn(request(base), ByteArray(100)) as TurnOutcome.Failed
        assertEquals(FailureKind.NO_TAILNET, outcome.failure.kind)
        assertNull(seen)
    }

    @Test
    fun sendsOverHttpWhenTheNameResolvesToTheTailnet() {
        val base = serve { respond(it, 200, wavReply) }
        val client = BridgeClient(resolve = { listOf(InetAddress.getByName("100.101.102.103")) })
        assertTrue(client.turn(request(base), ByteArray(100)) is TurnOutcome.Reply)
    }

    @Test
    fun sendsNothingWhenATailscaleAddressIsNotRouted() {
        // Tailscale off on a hostile network: the resolver answers with 100.64.0.1, but no
        // VPN route covers it, so the packets would go to the default gateway.
        val base = serve { respond(it, 200, wavReply) }
        var opened = 0
        val client = BridgeClient(
            resolve = { listOf(InetAddress.getByName("100.64.0.1")) },
            open = { opened++; it.openConnection(Proxy.NO_PROXY) },
            routed = { false },
        )
        val outcome = client.turn(request(base), ByteArray(100)) as TurnOutcome.Failed
        assertEquals(FailureKind.NOT_A_TAILNET_ADDRESS, outcome.failure.kind)
        assertEquals(0, opened)
        assertNull(seen)
    }

    @Test
    fun asksTheRouteCheckAboutEveryResolvedAddress() {
        val base = serve { respond(it, 200, wavReply) }
        val asked = mutableListOf<String>()
        val client = BridgeClient(
            resolve = { listOf(InetAddress.getByName("100.101.102.103"), InetAddress.getByName("fd7a:115c:a1e0::7")) },
            routed = { asked += it.hostAddress; it.hostAddress == "100.101.102.103" },
        )
        val outcome = client.turn(request(base), ByteArray(100)) as TurnOutcome.Failed
        assertEquals(FailureKind.NOT_A_TAILNET_ADDRESS, outcome.failure.kind)
        assertEquals(2, asked.size)
        assertNull(seen)
    }

    @Test
    fun connectsThroughTheInjectedOpenWithTheNameFromTheInjectedResolve() {
        val base = serve { respond(it, 200, wavReply) }
        val resolved = mutableListOf<String>()
        val openedUrls = mutableListOf<String>()
        val client = BridgeClient(
            resolve = { resolved += it; listOf(InetAddress.getByName("100.101.102.103")) },
            open = { openedUrls += it.toString(); it.openConnection(Proxy.NO_PROXY) },
            routed = { true },
        )
        assertTrue(client.turn(request(base), ByteArray(100)) is TurnOutcome.Reply)
        assertEquals(listOf("127.0.0.1"), resolved)
        assertEquals(listOf("$base/turn?project=demo"), openedUrls)
    }

    @Test
    fun neverAsksForAProxyByDefault() {
        val base = serve { respond(it, 200, wavReply) }
        val before = ProxySelector.getDefault()
        var asked = 0
        ProxySelector.setDefault(object : ProxySelector() {
            override fun select(uri: URI?): List<Proxy> {
                asked++
                return listOf(Proxy.NO_PROXY)
            }

            override fun connectFailed(uri: URI?, address: SocketAddress?, error: IOException?) = Unit
        })
        try {
            assertTrue(loopbackClient.turn(request(base), ByteArray(100)) is TurnOutcome.Reply)
        } finally {
            ProxySelector.setDefault(before)
        }
        assertEquals(0, asked)
    }
}
