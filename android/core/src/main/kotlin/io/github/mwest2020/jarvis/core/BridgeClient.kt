package io.github.mwest2020.jarvis.core

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.Proxy
import java.net.URL
import java.net.URLConnection

/**
 * Sends one turn with the platform's own HTTP client; no third-party library. Blocking:
 * call it off the main thread.
 *
 * The address range alone proves nothing: on a hostile network with Tailscale off, a
 * resolver can answer a `*.ts.net` name with 100.64.0.1 and the gateway can claim that
 * address. So the caller decides the route. On Android the app passes [resolve] and [open]
 * bound to the Tailscale VPN network and [routed] from that network's routes, and refuses
 * before it gets here when there is no such network. No proxy is ever used: a proxy would
 * read a plain-http `Authorization` header.
 *
 * For a plain-http request every resolved address must be a Tailscale address and be
 * [routed]; otherwise nothing is sent.
 */
class BridgeClient(
    private val resolve: (String) -> List<InetAddress> = { InetAddress.getAllByName(it).toList() },
    private val open: (URL) -> URLConnection = { it.openConnection(Proxy.NO_PROXY) },
    private val routed: (InetAddress) -> Boolean = { true },
    private val requireTailnetForCleartext: Boolean = true,
    private val connectTimeoutMs: Int = 10_000,
    private val readTimeoutMs: Int = 180_000,
) {
    fun turn(request: TurnRequest, wav: ByteArray): TurnOutcome {
        var connection: HttpURLConnection? = null
        return try {
            if (request.cleartext && requireTailnetForCleartext) {
                val addresses = resolve(request.url.host)
                if (addresses.isEmpty() || !addresses.all { Tailnet.isTailscaleAddress(it) && routed(it) }) {
                    return TurnOutcome.Failed(
                        TurnFailure(
                            FailureKind.NOT_A_TAILNET_ADDRESS,
                            "The bridge's name does not point at an address that Tailscale routes. Nothing was sent.",
                        ),
                    )
                }
            }
            connection = (open(request.url) as HttpURLConnection).apply {
                requestMethod = "POST"
                instanceFollowRedirects = false
                useCaches = false
                connectTimeout = connectTimeoutMs
                readTimeout = readTimeoutMs
                doOutput = true
                request.headers.forEach { (name, value) -> setRequestProperty(name, value) }
                // Buffered, not streamed: the recording is a few MB at most, and the
                // platform then sends the Content-Length the bridge requires.
            }
            connection.outputStream.use { it.write(wav) }
            val status = connection.responseCode
            if (status != 200) {
                val body = connection.errorStream?.use { readLimited(it, ERROR_BODY_LIMIT) }
                return TurnOutcome.Failed(TurnErrors.fromStatus(status, body?.toString(Charsets.UTF_8).orEmpty()))
            }
            val audio = connection.inputStream.use { readLimited(it, REPLY_LIMIT + 1) }
            if (audio.size < Wav.HEADER_SIZE || audio.size > REPLY_LIMIT) {
                return TurnOutcome.Failed(
                    TurnFailure(FailureKind.BAD_REPLY, "The bridge's reply is not playable audio."),
                )
            }
            TurnOutcome.Reply(
                wav = audio,
                summary = TurnErrors.decodeSummary(connection.getHeaderField("X-Jarvis-Summary")),
                sessionId = connection.getHeaderField("X-Jarvis-Session-Id"),
            )
        } catch (error: IOException) {
            TurnOutcome.Failed(TurnErrors.fromException(error))
        } finally {
            connection?.disconnect()
        }
    }

    private fun readLimited(stream: InputStream, limit: Int): ByteArray {
        val out = ByteArrayOutputStream()
        val chunk = ByteArray(16 * 1024)
        while (out.size() < limit) {
            val n = stream.read(chunk, 0, minOf(chunk.size, limit - out.size()))
            if (n < 0) break
            out.write(chunk, 0, n)
        }
        return out.toByteArray()
    }

    companion object {
        const val ERROR_BODY_LIMIT = 2_048
        const val REPLY_LIMIT = 32 * 1024 * 1024
    }
}
