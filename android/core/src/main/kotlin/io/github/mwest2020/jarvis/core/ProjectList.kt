package io.github.mwest2020.jarvis.core

import java.net.URI
import java.net.URL

/**
 * What the projects field in the settings means. Empty or `*`: ask the bridge
 * (`GET /projects`). Anything else: the typed names, as before.
 */
sealed interface ProjectSetting {
    data object FromBridge : ProjectSetting
    data class Named(val names: List<String>) : ProjectSetting

    companion object {
        const val FROM_BRIDGE = "*"

        fun parse(text: String): ProjectSetting {
            val names = text.split(',', '\n', ' ').map { it.trim() }.filter { it.isNotEmpty() }.distinct()
            return if (names.isEmpty() || names == listOf(FROM_BRIDGE)) FromBridge else Named(names)
        }

        /** How a setting is stored and shown in the settings field again. */
        fun format(setting: ProjectSetting): String = when (setting) {
            FromBridge -> FROM_BRIDGE
            is Named -> setting.names.joinToString(", ")
        }
    }
}

/** One `GET /projects`. Like [TurnRequest], [toString] leaves the headers (the token) out. */
class ListRequest(val url: URL, val headers: Map<String, String>) {
    val cleartext: Boolean get() = url.protocol.equals("http", ignoreCase = true)

    override fun toString(): String = "ListRequest(GET ${url.protocol}://${url.host}${url.path})"
}

sealed interface ListOutcome {
    data class Projects(val names: List<String>) : ListOutcome
    data class Failed(val failure: TurnFailure) : ListOutcome
}

object ProjectList {
    /** More than this from the bridge is not a project list. */
    const val MAX_PROJECTS = 1_000
    const val MAX_NAME_LENGTH = 200
    const val BODY_LIMIT = 256 * 1024

    /** `<base>/projects`. */
    fun url(base: URI): URL {
        val path = (base.rawPath ?: "").trimEnd('/') + "/projects"
        return URI("${base.scheme}://${base.rawAuthority}$path").toURL()
    }

    /** The same address and token rules as a turn ([TurnRequests.build]). */
    fun request(baseUrl: String, token: String?): Built<ListRequest> {
        val base = when (val checked = BridgeUrl.validate(baseUrl)) {
            is Built.Refused -> return checked
            is Built.Ok -> checked.value
        }
        if (token.isNullOrEmpty()) {
            return Built.Refused(TurnFailure(FailureKind.NOT_CONFIGURED, "Set the token in Settings."))
        }
        if (!TurnRequests.isValidToken(token)) {
            return Built.Refused(
                TurnFailure(
                    FailureKind.NOT_CONFIGURED,
                    "The token contains spaces or unusual characters. Enter it again in Settings.",
                ),
            )
        }
        val headers = mapOf("Authorization" to "Bearer $token", "Accept" to "application/json")
        return Built.Ok(ListRequest(url(base), headers))
    }

    /** A non-200 answer to `GET /projects`. */
    fun fromStatus(status: Int, body: String = ""): TurnFailure = when (status) {
        404 -> TurnFailure(
            FailureKind.BAD_REQUEST,
            "The bridge does not offer a project list. Update the bridge, or type the project names in Settings.",
        )
        else -> TurnErrors.fromStatus(status, body)
    }

    private val badReply = TurnFailure(FailureKind.BAD_REPLY, "The bridge's project list could not be read.")

    /**
     * Reads `{"projects": ["a", "b"]}`. Names are kept in the bridge's order, without
     * duplicates; an empty name, a control character or an over-long name makes the whole
     * reply unreadable rather than half-used.
     */
    fun parse(json: String): ListOutcome {
        val value = try {
            MiniJson(json).document()
        } catch (_: IllegalArgumentException) {
            return ListOutcome.Failed(badReply)
        }
        val list = (value as? Map<*, *>)?.get("projects") as? List<*> ?: return ListOutcome.Failed(badReply)
        if (list.size > MAX_PROJECTS) return ListOutcome.Failed(badReply)
        val names = list.map { item ->
            val name = item as? String ?: return ListOutcome.Failed(badReply)
            if (name.isBlank() || name.length > MAX_NAME_LENGTH || name.any { it.isISOControl() }) {
                return ListOutcome.Failed(badReply)
            }
            name
        }
        return ListOutcome.Projects(names.distinct())
    }
}

/**
 * Just enough JSON for the project list: objects, arrays, strings (with escapes), numbers,
 * true, false, null. No library: the app takes nothing beyond AndroidX and Kotlin (A9), and
 * `org.json` is not on a plain JVM, where these tests run.
 */
internal class MiniJson(private val text: String) {
    private var at = 0

    fun document(): Any? {
        val value = value(depth = 0)
        skipSpace()
        require(at == text.length) { "trailing data" }
        return value
    }

    private fun value(depth: Int): Any? {
        require(depth < 16) { "nested too deep" }
        skipSpace()
        require(at < text.length) { "unexpected end" }
        return when (text[at]) {
            '{' -> obj(depth)
            '[' -> array(depth)
            '"' -> string()
            't' -> literal("true", true)
            'f' -> literal("false", false)
            'n' -> literal("null", null)
            else -> number()
        }
    }

    private fun obj(depth: Int): Map<String, Any?> {
        val out = LinkedHashMap<String, Any?>()
        at++
        skipSpace()
        if (peek() == '}') { at++; return out }
        while (true) {
            skipSpace()
            require(peek() == '"') { "key expected" }
            val key = string()
            skipSpace()
            expect(':')
            out[key] = value(depth + 1)
            skipSpace()
            when (next()) {
                ',' -> continue
                '}' -> return out
                else -> throw IllegalArgumentException("',' or '}' expected")
            }
        }
    }

    private fun array(depth: Int): List<Any?> {
        val out = ArrayList<Any?>()
        at++
        skipSpace()
        if (peek() == ']') { at++; return out }
        while (true) {
            out += value(depth + 1)
            skipSpace()
            when (next()) {
                ',' -> continue
                ']' -> return out
                else -> throw IllegalArgumentException("',' or ']' expected")
            }
        }
    }

    private fun string(): String {
        expect('"')
        val out = StringBuilder()
        while (true) {
            val c = next()
            when {
                c == '"' -> return out.toString()
                c == '\\' -> when (val e = next()) {
                    '"', '\\', '/' -> out.append(e)
                    'b' -> out.append('\b')
                    'f' -> out.append('\u000C')
                    'n' -> out.append('\n')
                    'r' -> out.append('\r')
                    't' -> out.append('\t')
                    'u' -> {
                        require(at + 4 <= text.length) { "short \\u escape" }
                        val code = text.substring(at, at + 4).toIntOrNull(16)
                            ?: throw IllegalArgumentException("bad \\u escape")
                        out.append(code.toChar())
                        at += 4
                    }
                    else -> throw IllegalArgumentException("bad escape")
                }
                c < ' ' -> throw IllegalArgumentException("control character in a string")
                else -> out.append(c)
            }
        }
    }

    private fun number(): Double {
        val start = at
        while (at < text.length && text[at] in "+-0123456789.eE") at++
        return text.substring(start, at).toDoubleOrNull() ?: throw IllegalArgumentException("bad value")
    }

    private fun literal(word: String, value: Any?): Any? {
        require(text.startsWith(word, at)) { "bad literal" }
        at += word.length
        return value
    }

    private fun skipSpace() {
        while (at < text.length && text[at] in " \t\r\n") at++
    }

    private fun peek(): Char? = text.getOrNull(at)

    private fun next(): Char {
        require(at < text.length) { "unexpected end" }
        return text[at++]
    }

    private fun expect(c: Char) {
        require(next() == c) { "'$c' expected" }
    }
}
