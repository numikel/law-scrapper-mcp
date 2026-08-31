"""Tests for config helper functions (_host_of, is_loopback_entry)."""

from __future__ import annotations

from law_scrapper_mcp.config import _host_of, is_loopback_entry


class TestHostOf:
    """Tests for _host_of() — extract hostname from allowlist entry."""

    def test_strips_scheme(self) -> None:
        assert _host_of("https://example.com") == "example.com"
        assert _host_of("http://api.example.com:8080") == "api.example.com"

    def test_strips_port_ipv4(self) -> None:
        assert _host_of("127.0.0.1:8080") == "127.0.0.1"
        assert _host_of("192.168.1.1:443") == "192.168.1.1"

    def test_handles_bracketed_ipv6(self) -> None:
        assert _host_of("[::1]") == "::1"
        assert _host_of("[::1]:8080") == "::1"
        assert _host_of("[fe80::1]:443") == "fe80::1"

    def test_handles_bare_ipv6_loopback(self) -> None:
        """Bare IPv6 loopback (::1) must be recognized, not split on colon."""
        assert _host_of("::1") == "::1"

    def test_handles_bare_ipv6_link_local(self) -> None:
        """Bare link-local IPv6 must be recognized."""
        assert _host_of("fe80::1") == "fe80::1"

    def test_handles_bare_ipv6_all_zeros(self) -> None:
        """Bare IPv6 all-zeros must be recognized."""
        assert _host_of("::") == "::"

    def test_localhost(self) -> None:
        assert _host_of("localhost") == "localhost"
        assert _host_of("localhost:8080") == "localhost"

    def test_with_whitespace(self) -> None:
        assert _host_of("  127.0.0.1:8080  ") == "127.0.0.1"
        assert _host_of("  [::1]:443  ") == "::1"


class TestIsLoopbackEntry:
    """Tests for is_loopback_entry() — detect loopback allowlist entries."""

    def test_ipv4_loopback(self) -> None:
        assert is_loopback_entry("127.0.0.1")
        assert is_loopback_entry("127.0.0.1:8080")
        assert is_loopback_entry("127.255.255.255")

    def test_ipv4_non_loopback(self) -> None:
        assert not is_loopback_entry("0.0.0.0")
        assert not is_loopback_entry("192.168.1.1")
        assert not is_loopback_entry("8.8.8.8:53")

    def test_ipv6_loopback_bare(self) -> None:
        """Bare IPv6 loopback ::1 must be recognized."""
        assert is_loopback_entry("::1")

    def test_ipv6_loopback_bracketed(self) -> None:
        """Bracketed IPv6 loopback [::1] must be recognized."""
        assert is_loopback_entry("[::1]")
        assert is_loopback_entry("[::1]:8080")

    def test_ipv6_non_loopback(self) -> None:
        """Link-local fe80::1 is non-loopback."""
        assert not is_loopback_entry("fe80::1")
        assert not is_loopback_entry("[fe80::1]")

    def test_ipv6_all_zeros(self) -> None:
        """IPv6 all-zeros (::) is non-loopback."""
        assert not is_loopback_entry("::")
        assert not is_loopback_entry("[::]")

    def test_localhost_names(self) -> None:
        assert is_loopback_entry("localhost")
        assert is_loopback_entry("localhost:8080")

    def test_localhost_case_insensitive(self) -> None:
        assert is_loopback_entry("LOCALHOST")
        assert is_loopback_entry("LocalHost:443")

    def test_with_scheme(self) -> None:
        assert is_loopback_entry("http://127.0.0.1:8080")
        assert is_loopback_entry("https://localhost:443")
        assert is_loopback_entry("http://[::1]:8080")

    def test_invalid_ip_string(self) -> None:
        """Invalid IP strings should be treated as non-loopback."""
        assert not is_loopback_entry("not-an-ip")
        assert not is_loopback_entry("256.256.256.256")
