package io.github.mwest2020.jarvis

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.RouteInfo
import android.os.Build
import io.github.mwest2020.jarvis.core.BridgeClient
import io.github.mwest2020.jarvis.core.MatchingRoute
import io.github.mwest2020.jarvis.core.Tailnet
import java.net.Proxy

/**
 * Finds the Tailscale VPN network and builds a client that can only use it.
 *
 * A plain-http turn must travel through the tunnel, not merely towards an address in
 * Tailscale's range: with Tailscale off, a hostile Wi-Fi can answer the bridge's name with
 * 100.64.0.1 and take the packets at its gateway. So the name is resolved with the VPN
 * network's DNS, the socket is bound to the VPN network, every resolved address must be
 * covered by one of that network's routes, and no proxy is used.
 *
 * "Covered" means the most specific matching route is a unicast route. On Android 13+ a
 * VPN app can exclude a range from its tunnel (VpnService.Builder.excludeRoute); that shows
 * up here as a throw route, which RouteInfo.matches still matches although its packets
 * leave outside the tunnel (jarvis#16). Tailnet.carries decides.
 *
 * "The Tailscale network" is a network with the VPN transport whose own interface address
 * is a Tailscale address. Another VPN app that hands out such an address (100.64.0.0/10 is
 * the shared CGNAT range; Cloudflare WARP Zero Trust uses it too) also passes, and its DNS
 * and tunnel are then trusted like Tailscale's. The phone's owner chooses which VPN runs.
 */
class TailnetRoute(context: Context) {
    private val connectivity = context.getSystemService(ConnectivityManager::class.java)

    /** A client bound to the tunnel, or null when no Tailscale VPN network is up. */
    fun boundClient(): BridgeClient? {
        val (network, link) = tunnel() ?: return null
        return BridgeClient(
            resolve = { network.getAllByName(it).toList() },
            open = { network.openConnection(it, Proxy.NO_PROXY) },
            routed = { address ->
                Tailnet.carries(
                    link.routes.filter { it.matches(address) }
                        .map { MatchingRoute(it.destination.prefixLength, isUnicast(it)) },
                )
            },
        )
    }

    /** Before Android 13 a route has no type, and a VPN app cannot exclude a range either. */
    private fun isUnicast(route: RouteInfo): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU || route.type == RouteInfo.RTN_UNICAST

    private fun tunnel(): Pair<Network, LinkProperties>? {
        @Suppress("DEPRECATION") // allNetworks: a callback registration is too much for one lookup per turn.
        val candidates = listOfNotNull(connectivity.activeNetwork) + connectivity.allNetworks
        for (network in candidates) {
            val capabilities = connectivity.getNetworkCapabilities(network) ?: continue
            if (!capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue
            val link = connectivity.getLinkProperties(network) ?: continue
            if (link.linkAddresses.any { Tailnet.isTailscaleAddress(it.address) }) return network to link
        }
        return null
    }
}
