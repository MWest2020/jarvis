package io.github.mwest2020.jarvis.core

import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress

/**
 * One route of the VPN network that matches an address: how long its prefix is, and whether
 * it is a unicast route (packets go into the tunnel) and not a throw or unreachable route.
 */
data class MatchingRoute(val prefixLength: Int, val unicast: Boolean)

object Tailnet {
    /**
     * Whether the tunnel carries an address, given the routes of the VPN network that match
     * it ([matching]). The most specific route decides, as it does in the kernel: an address
     * the VPN app excluded (VpnService.Builder.excludeRoute, Android 13+) has a throw route
     * that is more specific than the unicast route around it, and its packets leave outside
     * the tunnel. No matching route, or a most specific one that is not unicast: not carried.
     */
    fun carries(matching: List<MatchingRoute>): Boolean {
        val longest = matching.maxOfOrNull { it.prefixLength } ?: return false
        return matching.filter { it.prefixLength == longest }.all { it.unicast }
    }

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
