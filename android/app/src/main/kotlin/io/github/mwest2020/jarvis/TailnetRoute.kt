package io.github.mwest2020.jarvis

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import io.github.mwest2020.jarvis.core.BridgeClient
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
 * "The Tailscale network" is a network with the VPN transport whose own interface address
 * is a Tailscale address. Another VPN app that hands out such an address would also pass;
 * the phone's owner chooses which VPN runs.
 */
class TailnetRoute(context: Context) {
    private val connectivity = context.getSystemService(ConnectivityManager::class.java)

    /** A client bound to the tunnel, or null when no Tailscale VPN network is up. */
    fun boundClient(): BridgeClient? {
        val (network, link) = tunnel() ?: return null
        return BridgeClient(
            resolve = { network.getAllByName(it).toList() },
            open = { network.openConnection(it, Proxy.NO_PROXY) },
            routed = { address -> link.routes.any { it.matches(address) } },
        )
    }

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
