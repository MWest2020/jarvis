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
            if (request.cleartext) notRouted(request.url)?.let { return TurnOutcome.Failed(it) }
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
            val contentType = connection.contentType
            if (StreamFormat.isStream(contentType)) {
                // jarvis-streaming-speech: the headers are in, the audio is still coming. The
                // connection is handed to the reply, which the player closes.
                val rate = StreamFormat.sampleRate(contentType) ?: return TurnOutcome.Failed(
                    TurnFailure(FailureKind.BAD_REPLY, "The bridge's reply is not playable audio."),
                )
                val open = connection
                val reply = StreamedReply(
                    summary = TurnErrors.decodeSummary(open.getHeaderField("X-Jarvis-Summary")),
                    sessionId = open.getHeaderField("X-Jarvis-Session-Id"),
                    projectsChanged = open.getHeaderField("X-Jarvis-Projects-Changed")?.trim() == "1",
                    needsConfirmation = open.getHeaderField("X-Jarvis-Needs-Confirmation")?.trim() == "1",
                    sampleRate = rate,
                    reader = PcmReader(open.inputStream),
                    onClose = { open.disconnect() },
                )
                connection = null
                return TurnOutcome.Streamed(reply)
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
                projectsChanged = connection.getHeaderField("X-Jarvis-Projects-Changed")?.trim() == "1",
            )
        } catch (error: IOException) {
            TurnOutcome.Failed(TurnErrors.fromException(error))
        } finally {
            connection?.disconnect()
        }
    }

    /**
     * `GET /projects` (add-project-list): the project names the bridge serves. The same
     * route rules as [turn]: for plain http every resolved address must be a routed
     * Tailscale address, no proxy, no redirects; the token goes to the configured host only.
     */
    fun projects(request: ListRequest): ListOutcome {
        var connection: HttpURLConnection? = null
        return try {
            if (request.cleartext) notRouted(request.url)?.let { return ListOutcome.Failed(it) }
            connection = (open(request.url) as HttpURLConnection).apply {
                requestMethod = "GET"
                instanceFollowRedirects = false
                useCaches = false
                connectTimeout = connectTimeoutMs
                readTimeout = LIST_READ_TIMEOUT_MS
                request.headers.forEach { (name, value) -> setRequestProperty(name, value) }
            }
            val status = connection.responseCode
            if (status != 200) {
                val body = connection.errorStream?.use { readLimited(it, ERROR_BODY_LIMIT) }
                return ListOutcome.Failed(ProjectList.fromStatus(status, body?.toString(Charsets.UTF_8).orEmpty()))
            }
            val body = connection.inputStream.use { readLimited(it, ProjectList.BODY_LIMIT + 1) }
            if (body.size > ProjectList.BODY_LIMIT) {
                return ListOutcome.Failed(
                    TurnFailure(FailureKind.BAD_REPLY, "The bridge's project list could not be read."),
                )
            }
            ProjectList.parse(body.toString(Charsets.UTF_8))
        } catch (error: IOException) {
            ListOutcome.Failed(TurnErrors.fromException(error))
        } finally {
            connection?.disconnect()
        }
    }

    /** Null when a plain-http request to [url] may go out; otherwise why not. Throws on a failed lookup. */
    private fun notRouted(url: URL): TurnFailure? {
        if (!requireTailnetForCleartext) return null
        val addresses = resolve(url.host)
        if (addresses.isNotEmpty() && addresses.all { Tailnet.isTailscaleAddress(it) && routed(it) }) return null
        return TurnFailure(
            FailureKind.NOT_A_TAILNET_ADDRESS,
            "The bridge's name does not point at an address that Tailscale routes. Nothing was sent.",
        )
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
        const val LIST_READ_TIMEOUT_MS = 20_000
    }
}
