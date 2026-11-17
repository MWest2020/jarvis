package io.github.mwest2020.jarvis.core

import java.net.URI
import java.net.URISyntaxException
import java.net.URL
import java.net.URLEncoder

/**
 * One `POST /turn` to the bridge. [toString] leaves the headers out, so the token cannot end
 * up in a log line or a crash report by accident.
 */
class TurnRequest(val url: URL, val headers: Map<String, String>) {
    val cleartext: Boolean get() = url.protocol.equals("http", ignoreCase = true)

    override fun toString(): String = "TurnRequest(POST ${url.protocol}://${url.host}${url.path})"
}

sealed interface Built<out T> {
    data class Ok<T>(val value: T) : Built<T>
    data class Refused(val failure: TurnFailure) : Built<Nothing>
}

object BridgeUrl {
    /** Plain HTTP is only accepted for names under this suffix (Tailscale MagicDNS). */
    const val TAILNET_SUFFIX = ".ts.net"

    /**
     * Checks the bridge address from the settings and returns it without a trailing slash.
     *
     * `https://` is accepted for any host. `http://` only for a `*.ts.net` name: on a tailnet
     * WireGuard encrypts underneath, anywhere else the token would travel in the clear. The
     * same rule is in the app's network security config, which cannot express the Tailscale
     * address range; [BridgeClient] checks the resolved address for that.
     */
    fun validate(raw: String): Built<URI> {
        val text = raw.trim().trimEnd('/')
        if (text.isEmpty()) {
            return refuse(FailureKind.NOT_CONFIGURED, "Set the bridge address in Settings.")
        }
        val uri = try {
            URI(text)
        } catch (_: URISyntaxException) {
            return refuse(FailureKind.INVALID_URL, "The bridge address is not a valid URL.")
        }
        val scheme = uri.scheme?.lowercase()
        val host = uri.host?.lowercase()
        if (host.isNullOrEmpty() || scheme !in setOf("http", "https")) {
            return refuse(
                FailureKind.INVALID_URL,
                "The bridge address must look like http://name.tailnet.ts.net:8787",
            )
        }
        if (uri.rawUserInfo != null || uri.rawQuery != null || uri.rawFragment != null) {
            return refuse(
                FailureKind.INVALID_URL,
                "The bridge address must not contain a user name, a query or a fragment.",
            )
        }
        if (scheme == "http" && !host.endsWith(TAILNET_SUFFIX)) {
            return refuse(
                FailureKind.CLEARTEXT_NOT_ALLOWED,
                "Plain http is only allowed for a Tailscale name (*.ts.net). Use that name, or https.",
            )
        }
        return Built.Ok(uri)
    }

    /** `<base>/turn?project=<name>`, the project name percent-encoded. */
    fun turnUrl(base: URI, project: String): URL {
        val path = (base.rawPath ?: "").trimEnd('/') + "/turn"
        val query = "project=" + URLEncoder.encode(project, Charsets.UTF_8).replace("+", "%20")
        return URI("${base.scheme}://${base.rawAuthority}$path?$query").toURL()
    }

    private fun refuse(kind: FailureKind, message: String) = Built.Refused(TurnFailure(kind, message))
}

object TurnRequests {
    /** The header that tells the bridge the app shows typed project names (jarvis-general-conversation D53). */
    const val TYPED_LIST_HEADER = "X-Jarvis-Projects"
    const val TYPED_LIST_VALUE = "typed"

    /**
     * Builds the request for one turn, or says what is missing. The token must be visible
     * ASCII without spaces, so it can never break out of the `Authorization` header.
     * [typedList]: the projects come from the settings, not from the bridge's list, so a new
     * project has to be added there by hand; the bridge then says so.
     */
    fun build(baseUrl: String, token: String?, project: String?, typedList: Boolean = false): Built<TurnRequest> {
        val base = when (val checked = BridgeUrl.validate(baseUrl)) {
            is Built.Refused -> return checked
            is Built.Ok -> checked.value
        }
        if (token.isNullOrEmpty()) {
            return refuse(FailureKind.NOT_CONFIGURED, "Set the token in Settings.")
        }
        if (!isValidToken(token)) {
            return refuse(
                FailureKind.NOT_CONFIGURED,
                "The token contains spaces or unusual characters. Enter it again in Settings.",
            )
        }
        if (project.isNullOrBlank()) {
            return refuse(FailureKind.NOT_CONFIGURED, "Pick a project first.")
        }
        val headers = buildMap {
            put("Authorization", "Bearer $token")
            put("Content-Type", "audio/wav")
            // A streamed reply first (jarvis-streaming-speech S1); an older bridge ignores
            // it and sends the WAV.
            put("Accept", StreamFormat.ACCEPT)
            if (typedList) put(TYPED_LIST_HEADER, TYPED_LIST_VALUE)
        }
        return Built.Ok(TurnRequest(BridgeUrl.turnUrl(base, project.trim()), headers))
    }

    fun isValidToken(token: String): Boolean =
        token.isNotEmpty() && token.all { it.code in 0x21..0x7E }

    private fun refuse(kind: FailureKind, message: String) = Built.Refused(TurnFailure(kind, message))
}
