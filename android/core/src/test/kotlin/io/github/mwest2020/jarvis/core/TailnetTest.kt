package io.github.mwest2020.jarvis.core

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress

class TailnetTest {
    private fun ip(text: String) = InetAddress.getByName(text)

    @Test
    fun acceptsTheCgnatRangeOnly() {
        assertTrue(Tailnet.isTailscaleAddress(ip("100.64.0.0")))
        assertTrue(Tailnet.isTailscaleAddress(ip("100.101.102.103")))
        assertTrue(Tailnet.isTailscaleAddress(ip("100.127.255.255")))
        assertFalse(Tailnet.isTailscaleAddress(ip("100.63.255.255")))
        assertFalse(Tailnet.isTailscaleAddress(ip("100.128.0.0")))
        assertFalse(Tailnet.isTailscaleAddress(ip("192.168.1.10")))
        assertFalse(Tailnet.isTailscaleAddress(ip("10.0.0.1")))
        assertFalse(Tailnet.isTailscaleAddress(ip("127.0.0.1")))
        assertFalse(Tailnet.isTailscaleAddress(ip("8.8.8.8")))
    }

    @Test
    fun acceptsTailscalesIpv6PrefixOnly() {
        assertTrue(Tailnet.isTailscaleAddress(ip("fd7a:115c:a1e0::1")))
        assertTrue(Tailnet.isTailscaleAddress(ip("fd7a:115c:a1e0:ab12:4843:cd96:6265:6667")))
        assertFalse(Tailnet.isTailscaleAddress(ip("fd7a:115c:a1e1::1")))
        assertFalse(Tailnet.isTailscaleAddress(ip("2001:db8::1")))
        assertFalse(Tailnet.isTailscaleAddress(ip("::1")))
    }
}
