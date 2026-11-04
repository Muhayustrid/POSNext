package id.posnext.shell

import java.net.URI

/** Server URL rules, kept free of Android types so they are unit tested. */
object ServerUrl {
    /** Trimmed URL when it is http(s) with a host, else null. */
    fun normalize(input: String?): String? {
        val raw = input?.trim().orEmpty()
        val uri = runCatching { URI(raw) }.getOrNull() ?: return null
        val scheme = uri.scheme?.lowercase()
        if (scheme != "http" && scheme != "https") return null
        if (uri.host.isNullOrEmpty()) return null
        return raw
    }

    /**
     * The one allowedOriginRule for addWebMessageListener: SCHEME://HOST[:PORT],
     * no path. The port is kept only when explicit (default ports are implied).
     */
    fun originRule(url: String): String {
        val uri = URI(url)
        val port = if (uri.port == -1) "" else ":${uri.port}"
        return "${uri.scheme.lowercase()}://${uri.host.lowercase()}$port"
    }
}
