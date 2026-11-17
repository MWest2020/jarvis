package io.github.mwest2020.jarvis.core

import com.sun.net.httpserver.HttpServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.URI

/** add-project-list: the projects setting, `GET /projects` and its reply. */
class ProjectListTest {
    // --- the setting ---

    @Test
    fun emptyOrStarMeansAskTheBridge() {
        assertEquals(ProjectSetting.FromBridge, ProjectSetting.parse(""))
        assertEquals(ProjectSetting.FromBridge, ProjectSetting.parse("   \n "))
        assertEquals(ProjectSetting.FromBridge, ProjectSetting.parse("*"))
        assertEquals(ProjectSetting.FromBridge, ProjectSetting.parse(" * "))
    }

    @Test
    fun typedNamesStillWork() {
        assertEquals(ProjectSetting.Named(listOf("demo", "other")), ProjectSetting.parse("demo, other,demo"))
        assertEquals(ProjectSetting.Named(listOf("demo")), ProjectSetting.parse("demo\n"))
    }

    @Test
    fun theSettingIsShownAgainAsTyped() {
        assertEquals("*", ProjectSetting.format(ProjectSetting.parse("")))
        assertEquals("a, b", ProjectSetting.format(ProjectSetting.parse("a,b")))
        assertEquals(ProjectSetting.parse("a,b"), ProjectSetting.parse(ProjectSetting.format(ProjectSetting.parse("a,b"))))
    }

    // --- the request ---

    @Test
    fun buildsTheListUrlNextToTurn() {
        val built = ProjectList.request("http://jarvis.example.ts.net:8787/", "tok") as Built.Ok
        assertEquals("http://jarvis.example.ts.net:8787/projects", built.value.url.toString())
        assertEquals("Bearer tok", built.value.headers["Authorization"])
        assertTrue(built.value.cleartext)
        assertFalse(built.value.toString().contains("tok"))
        assertEquals(
            "https://h.example/base/projects",
            ProjectList.url(URI("https://h.example/base/")).toString(),
        )
    }

    @Test
    fun theListFollowsTheSameAddressAndTokenRules() {
        assertTrue(ProjectList.request("http://plain.example.com", "tok") is Built.Refused)
        assertTrue(ProjectList.request("http://jarvis.x.ts.net", null) is Built.Refused)
        assertTrue(ProjectList.request("http://jarvis.x.ts.net", "has space") is Built.Refused)
    }

    // --- the reply ---

    @Test
    fun readsTheBridgesReply() {
        assertEquals(
            ListOutcome.Projects(listOf("bazibo", "jarvis")),
            ProjectList.parse("""{"projects": ["bazibo", "jarvis", "bazibo"]}"""),
        )
        assertEquals(ListOutcome.Projects(emptyList()), ProjectList.parse("""{"projects":[]}"""))
        assertEquals(
            ListOutcome.Projects(listOf("café")),
            ProjectList.parse("""{"other": {"x": [1, true, null]}, "projects": ["café"]}"""),
        )
    }

    @Test
    fun refusesWhatIsNotAProjectList() {
        val bad = listOf(
            "", "[]", "{}", """{"projects": "a"}""", """{"projects": [1]}""", """{"projects": [""]}""",
            """{"projects": ["a\nb"]}""", """{"projects": ["a"]} trailing""", """{"projects": ["a"""",
            """{"projects": ["${"x".repeat(ProjectList.MAX_NAME_LENGTH + 1)}"]}""",
            "[".repeat(100),
        )
        for (json in bad) {
            val outcome = ProjectList.parse(json)
            assertTrue("accepted: $json", outcome is ListOutcome.Failed)
            assertEquals(FailureKind.BAD_REPLY, (outcome as ListOutcome.Failed).failure.kind)
        }
    }

    // --- the exchange, against a loopback server ---

    private var server: HttpServer? = null
    private var seenMethod: String? = null
    private var seenPath: String? = null
    private var seenAuth: String? = null

    private fun serve(status: Int, body: String, headers: Map<String, String> = emptyMap()): String {
        val started = HttpServer.create(InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0)
        started.createContext("/") { exchange ->
            seenMethod = exchange.requestMethod
            seenPath = exchange.requestURI.toString()
            seenAuth = exchange.requestHeaders.getFirst("Authorization")
            headers.forEach { (k, v) -> exchange.responseHeaders.add(k, v) }
            val bytes = body.toByteArray()
            exchange.sendResponseHeaders(status, if (bytes.isEmpty()) -1 else bytes.size.toLong())
            if (bytes.isNotEmpty()) exchange.responseBody.use { it.write(bytes) }
            exchange.close()
        }
        started.start()
        server = started
        return "http://127.0.0.1:${started.address.port}"
    }

    @After
    fun stop() {
        server?.stop(0)
    }

    private fun request(base: String) = ListRequest(
        ProjectList.url(URI(base)),
        mapOf("Authorization" to "Bearer s3cret", "Accept" to "application/json"),
    )

    private val loopbackClient = BridgeClient(requireTailnetForCleartext = false, readTimeoutMs = 5_000)

    @Test
    fun fetchesTheList() {
        val base = serve(200, """{"projects": ["a", "b"]}""", mapOf("Content-Type" to "application/json"))
        assertEquals(ListOutcome.Projects(listOf("a", "b")), loopbackClient.projects(request(base)))
        assertEquals("GET", seenMethod)
        assertEquals("/projects", seenPath)
        assertEquals("Bearer s3cret", seenAuth)
    }

    @Test
    fun aRefusedTokenSaysSo() {
        val base = serve(401, "missing or invalid token")
        val outcome = loopbackClient.projects(request(base)) as ListOutcome.Failed
        assertEquals(FailureKind.UNAUTHORIZED, outcome.failure.kind)
        assertFalse(outcome.failure.message.contains("s3cret"))
    }

    @Test
    fun anOlderBridgeWithoutTheRouteSaysWhatToDo() {
        val base = serve(404, "not found")
        val outcome = loopbackClient.projects(request(base)) as ListOutcome.Failed
        assertTrue(outcome.failure.message.contains("does not offer a project list"))
    }

    @Test
    fun doesNotFollowARedirect() {
        val base = serve(302, "", mapOf("Location" to "http://elsewhere.example/projects"))
        val outcome = loopbackClient.projects(request(base)) as ListOutcome.Failed
        assertEquals(FailureKind.SERVER_ERROR, outcome.failure.kind)
    }

    @Test
    fun anOversizedReplyIsRefused() {
        val names = (1..20_000).joinToString(",") { "\"project-number-$it\"" }
        val base = serve(200, """{"projects": [$names]}""")
        val outcome = loopbackClient.projects(request(base)) as ListOutcome.Failed
        assertEquals(FailureKind.BAD_REPLY, outcome.failure.kind)
    }

    @Test
    fun sendsNothingOverHttpWhenTheAddressIsNotRouted() {
        val base = serve(200, """{"projects": ["a"]}""")
        var opened = 0
        val client = BridgeClient(
            resolve = { listOf(InetAddress.getByName("100.64.0.1")) },
            open = { opened++; it.openConnection(Proxy.NO_PROXY) },
            routed = { false },
        )
        val outcome = client.projects(request(base)) as ListOutcome.Failed
        assertEquals(FailureKind.NOT_A_TAILNET_ADDRESS, outcome.failure.kind)
        assertEquals(0, opened)
        assertNull(seenPath)
    }

    @Test
    fun usesTheInjectedRouteForPlainHttp() {
        val base = serve(200, """{"projects": ["a"]}""")
        val resolved = mutableListOf<String>()
        val client = BridgeClient(
            resolve = { resolved += it; listOf(InetAddress.getByName("100.101.102.103")) },
            open = { it.openConnection(Proxy.NO_PROXY) },
            routed = { true },
        )
        assertEquals(ListOutcome.Projects(listOf("a")), client.projects(request(base)))
        assertEquals(listOf("127.0.0.1"), resolved)
    }

    @Test
    fun aClosedPortReadsAsNoTailnet() {
        val base = serve(200, "{}")
        server?.stop(0)
        val outcome = loopbackClient.projects(request(base)) as ListOutcome.Failed
        assertEquals(FailureKind.NO_TAILNET, outcome.failure.kind)
    }
}
