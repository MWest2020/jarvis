package io.github.mwest2020.jarvis.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TurnRequestTest {
    private fun ok(base: String, token: String? = "s3cret-token", project: String? = "demo"): TurnRequest =
        (TurnRequests.build(base, token, project) as Built.Ok).value

    private fun refused(base: String, token: String? = "s3cret-token", project: String? = "demo"): TurnFailure =
        (TurnRequests.build(base, token, project) as Built.Refused).failure

    @Test
    fun buildsTheTurnUrlWithTheProject() {
        val request = ok("http://jarvis.example.ts.net:8787")
        assertEquals("http://jarvis.example.ts.net:8787/turn?project=demo", request.url.toString())
        assertTrue(request.cleartext)
    }

    @Test
    fun toleratesSpacesATrailingSlashAndAPathPrefix() {
        assertEquals(
            "http://jarvis.example.ts.net:8787/turn?project=demo",
            ok("  http://jarvis.example.ts.net:8787/ ").url.toString(),
        )
        assertEquals(
            "https://host.example/jarvis/turn?project=demo",
            ok("https://host.example/jarvis/").url.toString(),
        )
    }

    @Test
    fun percentEncodesTheProjectName() {
        assertEquals(
            "http://jarvis.example.ts.net:8787/turn?project=my%20app%26x%3D1",
            ok("http://jarvis.example.ts.net:8787", project = "my app&x=1").url.toString(),
        )
    }

    @Test
    fun sendsTheTokenAsBearerAndTheBodyAsWav() {
        val request = ok("http://jarvis.example.ts.net:8787")
        assertEquals("Bearer s3cret-token", request.headers["Authorization"])
        assertEquals("audio/wav", request.headers["Content-Type"])
    }

    @Test
    fun neverPrintsTheToken() {
        val text = ok("http://jarvis.example.ts.net:8787").toString()
        assertFalse(text.contains("s3cret-token"))
        assertFalse(text.contains("Bearer"))
    }

    @Test
    fun refusesPlainHttpOutsideTheTailnetName() {
        assertEquals(FailureKind.CLEARTEXT_NOT_ALLOWED, refused("http://example.com:8787").kind)
        assertEquals(FailureKind.CLEARTEXT_NOT_ALLOWED, refused("http://192.168.1.10:8787").kind)
        assertEquals(FailureKind.CLEARTEXT_NOT_ALLOWED, refused("http://100.101.102.103:8787").kind)
        // A look-alike suffix is not the tailnet.
        assertEquals(FailureKind.CLEARTEXT_NOT_ALLOWED, refused("http://evil-ts.net:8787").kind)
        assertEquals(FailureKind.CLEARTEXT_NOT_ALLOWED, refused("http://jarvis.ts.net.evil.example").kind)
    }

    @Test
    fun acceptsHttpsForAnyHostAndHttpForTailnetNamesInAnyCase() {
        assertFalse(ok("https://bridge.example").cleartext)
        assertTrue(ok("HTTP://Jarvis.Example.TS.NET:8787").cleartext)
    }

    @Test
    fun refusesAddressesThatAreNotABridgeBase() {
        assertEquals(FailureKind.NOT_CONFIGURED, refused("").kind)
        assertEquals(FailureKind.INVALID_URL, refused("jarvis.example.ts.net:8787").kind)
        assertEquals(FailureKind.INVALID_URL, refused("ftp://jarvis.example.ts.net").kind)
        assertEquals(FailureKind.INVALID_URL, refused("http://user:pw@jarvis.example.ts.net:8787").kind)
        assertEquals(FailureKind.INVALID_URL, refused("http://jarvis.example.ts.net:8787/?x=1").kind)
        assertEquals(FailureKind.INVALID_URL, refused("http://not a url").kind)
    }

    @Test
    fun refusesAMissingOrUnsafeTokenAndAMissingProject() {
        val base = "http://jarvis.example.ts.net:8787"
        assertEquals(FailureKind.NOT_CONFIGURED, refused(base, token = null).kind)
        assertEquals(FailureKind.NOT_CONFIGURED, refused(base, token = "").kind)
        assertEquals(FailureKind.NOT_CONFIGURED, refused(base, token = "abc\r\nX-Injected: 1").kind)
        assertEquals(FailureKind.NOT_CONFIGURED, refused(base, token = "two words").kind)
        assertEquals(FailureKind.NOT_CONFIGURED, refused(base, project = " ").kind)
        assertEquals(FailureKind.NOT_CONFIGURED, refused(base, project = null).kind)
    }

    @Test
    fun aRefusalNeverRepeatsTheToken() {
        val failure = refused("http://jarvis.example.ts.net:8787", token = "bad token-value")
        assertFalse(failure.message.contains("token-value"))
    }
}
