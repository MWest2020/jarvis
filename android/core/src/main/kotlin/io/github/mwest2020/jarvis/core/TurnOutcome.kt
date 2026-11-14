package io.github.mwest2020.jarvis.core

import java.io.IOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.URLDecoder
import java.net.UnknownHostException

enum class FailureKind {
    NOT_CONFIGURED,
    INVALID_URL,
    CLEARTEXT_NOT_ALLOWED,
    NOT_A_TAILNET_ADDRESS,
    NO_TAILNET,
    TIMEOUT,
    NETWORK,
    UNAUTHORIZED,
    BUSY,
    BAD_REQUEST,
    TOO_LARGE,
    AGENT_FAILED,
    SERVER_ERROR,
    BAD_REPLY,
    RECORDING,
}

/** What went wrong, with one short sentence that is shown on the screen as it is. */
data class TurnFailure(val kind: FailureKind, val message: String)

sealed interface TurnOutcome {
    class Reply(val wav: ByteArray, val summary: String, val sessionId: String?) : TurnOutcome
    data class Failed(val failure: TurnFailure) : TurnOutcome
}

object TurnErrors {
    private const val DETAIL_LIMIT = 160

    /** Maps a non-200 status of `POST /turn` to a message. [body] is the bridge's own text. */
    fun fromStatus(status: Int, body: String = ""): TurnFailure = when (status) {
        401 -> TurnFailure(FailureKind.UNAUTHORIZED, "The bridge refused the token. Check the token in Settings.")
        409 -> TurnFailure(FailureKind.BUSY, "This project is busy with another turn. Try again in a moment.")
        413 -> TurnFailure(FailureKind.TOO_LARGE, "The recording is too large for the bridge. Keep it shorter.")
        400 -> TurnFailure(FailureKind.BAD_REQUEST, withDetail("The bridge did not accept the request", body))
        404 -> TurnFailure(
            FailureKind.BAD_REQUEST,
            "Nothing answers at this address as a Jarvis bridge. Check the address in Settings.",
        )
        502 -> TurnFailure(FailureKind.AGENT_FAILED, "The agent turn failed on the bridge. Try again; the bridge log has the details.")
        else -> TurnFailure(FailureKind.SERVER_ERROR, "The bridge answered with an error ($status).")
    }

    /** Maps a failure before or during the exchange to a message. */
    fun fromException(error: IOException): TurnFailure = when {
        error is UnknownHostException -> TurnFailure(
            FailureKind.NO_TAILNET,
            "The bridge's name does not resolve. Is Tailscale on?",
        )
        error is SocketTimeoutException -> TurnFailure(
            FailureKind.TIMEOUT,
            "No answer from the bridge in time. Check Tailscale, then try again.",
        )
        error is ConnectException || error is NoRouteToHostException -> TurnFailure(
            FailureKind.NO_TAILNET,
            "The bridge cannot be reached. Is Tailscale on, and is the bridge running?",
        )
        error.message?.contains("cleartext", ignoreCase = true) == true -> TurnFailure(
            FailureKind.CLEARTEXT_NOT_ALLOWED,
            "Plain http is only allowed for a Tailscale name (*.ts.net).",
        )
        else -> TurnFailure(FailureKind.NETWORK, "The connection to the bridge broke. Try again.")
    }

    /** The bridge sends `X-Jarvis-Summary` percent-encoded (Python's `urllib.parse.quote`). */
    fun decodeSummary(header: String?): String {
        if (header.isNullOrBlank()) return ""
        return try {
            URLDecoder.decode(header.replace("+", "%2B"), Charsets.UTF_8).trim()
        } catch (_: IllegalArgumentException) {
            header.trim()
        }
    }

    private fun withDetail(prefix: String, body: String): String {
        val detail = body.lineSequence().firstOrNull()?.trim().orEmpty()
            .filter { !it.isISOControl() }.take(DETAIL_LIMIT)
        return if (detail.isEmpty()) "$prefix." else "$prefix: $detail"
    }
}
