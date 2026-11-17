package io.github.mwest2020.jarvis.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

class TurnErrorsTest {
    @Test
    fun aRefusedTokenPointsAtTheToken() {
        val failure = TurnErrors.fromStatus(401, "missing or invalid token")
        assertEquals(FailureKind.UNAUTHORIZED, failure.kind)
        assertTrue(failure.message.contains("Check the token"))
    }

    @Test
    fun mapsTheBridgesStatuses() {
        assertEquals(FailureKind.BUSY, TurnErrors.fromStatus(409).kind)
        assertEquals(FailureKind.BUSY, TurnErrors.fromStatus(503).kind)
        assertTrue(TurnErrors.fromStatus(503).message.contains("busy"))
        assertEquals(FailureKind.TOO_LARGE, TurnErrors.fromStatus(413).kind)
        assertEquals(FailureKind.AGENT_FAILED, TurnErrors.fromStatus(502).kind)
        assertEquals(FailureKind.BAD_REQUEST, TurnErrors.fromStatus(400).kind)
        assertEquals(FailureKind.BAD_REQUEST, TurnErrors.fromStatus(404).kind)
        assertEquals(FailureKind.SERVER_ERROR, TurnErrors.fromStatus(500).kind)
        assertEquals(FailureKind.SERVER_ERROR, TurnErrors.fromStatus(302).kind)
        assertTrue(TurnErrors.fromStatus(409).message.contains("busy"))
    }

    @Test
    fun aBadRequestShowsTheBridgesFirstLineOnly() {
        val failure = TurnErrors.fromStatus(400, "unknown project: nope\nsecond line\u0007")
        assertEquals("The bridge did not accept the request: unknown project: nope", failure.message)
        assertEquals("The bridge did not accept the request.", TurnErrors.fromStatus(400, "").message)
        assertTrue(TurnErrors.fromStatus(400, "x".repeat(5000)).message.length < 220)
    }

    @Test
    fun noTailnetIsNamedAsSuch() {
        for (error in listOf(UnknownHostException("jarvis"), ConnectException("refused"), NoRouteToHostException())) {
            val failure = TurnErrors.fromException(error)
            assertEquals(FailureKind.NO_TAILNET, failure.kind)
            assertTrue(failure.message.contains("Tailscale"))
        }
    }

    @Test
    fun mapsTimeoutsBlockedCleartextAndOtherNetworkErrors() {
        assertEquals(FailureKind.TIMEOUT, TurnErrors.fromException(SocketTimeoutException()).kind)
        assertEquals(
            FailureKind.CLEARTEXT_NOT_ALLOWED,
            TurnErrors.fromException(IOException("Cleartext HTTP traffic to example.com not permitted")).kind,
        )
        assertEquals(FailureKind.NETWORK, TurnErrors.fromException(IOException("reset")).kind)
    }

    @Test
    fun noVpnNetworkReadsAsTailscaleOff() {
        val failure = TurnErrors.tailscaleOff()
        assertEquals(FailureKind.NO_TAILNET, failure.kind)
        assertTrue(failure.message.contains("Tailscale is off"))
        assertTrue(failure.message.contains("Nothing was sent"))
    }

    @Test
    fun aBrokenConnectionHintsAtTheToken() {
        val failure = TurnErrors.fromException(java.net.SocketException("Broken pipe"))
        assertEquals(FailureKind.NETWORK, failure.kind)
        assertTrue(failure.message.contains("check the token"))
    }

    @Test
    fun anErrorMessageNeverCarriesTheExceptionText() {
        val failure = TurnErrors.fromException(IOException("Bearer s3cret-token"))
        assertFalse(failure.message.contains("s3cret"))
    }

    @Test
    fun decodesTheSummaryHeader() {
        assertEquals(
            "Jarvis lets you talk, hands-free: 100% yours.",
            TurnErrors.decodeSummary("Jarvis%20lets%20you%20talk%2C%20hands-free%3A%20100%25%20yours."),
        )
        assertEquals("café + tea", TurnErrors.decodeSummary("caf%C3%A9%20+%20tea"))
        assertEquals("", TurnErrors.decodeSummary(null))
        assertEquals("50% done", TurnErrors.decodeSummary("50% done"))
    }
}
