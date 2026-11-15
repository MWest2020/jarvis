package io.github.mwest2020.jarvis.core

import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress

object Tailnet {
    /**
     * True for the addresses Tailscale hands out: 100.64.0.0/10 (the CGNAT range) and
     * fd7a:115c:a1e0::/48.
     */
    fun isTailscaleAddress(address: InetAddress): Boolean {
        val b = address.address
        return when (address) {
            is Inet4Address -> (b[0].toInt() and 0xFF) == 100 && (b[1].toInt() and 0xC0) == 0x40
            is Inet6Address ->
                (b[0].toInt() and 0xFF) == 0xFD && (b[1].toInt() and 0xFF) == 0x7A &&
                    (b[2].toInt() and 0xFF) == 0x11 && (b[3].toInt() and 0xFF) == 0x5C &&
                    (b[4].toInt() and 0xFF) == 0xA1 && (b[5].toInt() and 0xFF) == 0xE0
            else -> false
        }
    }
}
