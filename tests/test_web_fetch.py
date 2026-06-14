"""Tests for the best-effort live-site capture used by brand intake.

No test here touches the network. The SSRF gate is exercised against URLs whose
hosts are bare IP literals (classified without DNS) and one DNS-backed host whose
resolution is monkeypatched, and the HTML extractor is fed in-memory HTML. The
fetch_page_text coroutine itself is exercised only through its SSRF gate (which
short-circuits before any connection) so it still never hits the internet.

What is proven:
  1. The SSRF gate returns None / False for loopback (127.0.0.1), the cloud
     metadata endpoint (169.254.169.254), RFC-1918 (10.0.0.1), the unspecified
     address, and a non-http scheme; it allows a public host.
  2. A host that resolves to an internal address is blocked even with a benign
     hostname (DNS-rebinding-style smuggling).
  3. The HTMLParser extractor pulls the title, meta description, headings, and
     visible body text from a small document and strips <script>/<style>.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from brand_ops.agents.web_fetch import (
    PageExtract,
    _host_is_safe,
    _is_blocked_ip,
    _page_extract_from_html,
    _url_is_fetchable,
    fetch_page_text,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ─────────────────────────── SSRF gate ──────────────────────────────────────


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",        # loopback
        "169.254.169.254",  # cloud metadata (link-local)
        "169.254.0.1",      # link-local
        "10.0.0.1",         # RFC-1918 10/8
        "172.16.5.4",       # RFC-1918 172.16/12
        "192.168.1.1",      # RFC-1918 192.168/16
        "0.0.0.0",          # unspecified
        "::1",              # IPv6 loopback
        "fe80::1",          # IPv6 link-local
        "fc00::1",          # IPv6 unique-local (is_private)
        "not-an-ip",        # unparseable -> unsafe
    ],
)
def test_blocked_ips_are_unsafe(ip: str) -> None:
    assert _is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:2800:220:1::1"])
def test_public_ips_are_safe(ip: str) -> None:
    assert _is_blocked_ip(ip) is False


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://127.0.0.1:8080/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1",
        "https://192.168.1.1/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "ftp://example.com/file",    # non-http scheme
        "file:///etc/passwd",        # non-http scheme
        "gopher://example.com/",     # non-http scheme
        "javascript:alert(1)",       # non-http scheme
        "",                          # empty
    ],
)
def test_url_is_not_fetchable(url: str) -> None:
    assert _url_is_fetchable(url) is False


def test_public_ip_literal_url_is_fetchable() -> None:
    # A public IP literal needs no DNS, so this stays offline.
    assert _url_is_fetchable("http://8.8.8.8/") is True


def test_hostname_resolving_to_internal_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A benign hostname that resolves to a private address must be blocked.

    Guards the DNS-rebinding-style case the IP-literal tests cannot reach.
    """

    def fake_getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        # (family, type, proto, canonname, sockaddr)
        return [(0, 0, 0, "", ("10.0.0.5", 0))]

    monkeypatch.setattr("brand_ops.agents.web_fetch.socket.getaddrinfo", fake_getaddrinfo)
    assert _host_is_safe("evil.example.com") is False
    assert _url_is_fetchable("http://evil.example.com/") is False


def test_hostname_resolving_to_public_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        return [(0, 0, 0, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("brand_ops.agents.web_fetch.socket.getaddrinfo", fake_getaddrinfo)
    assert _host_is_safe("example.com") is True
    assert _url_is_fetchable("https://example.com/about") is True


def test_hostname_with_any_internal_address_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a host resolves to a public AND a private address, block it."""

    def fake_getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        return [
            (0, 0, 0, "", ("93.184.216.34", 0)),
            (0, 0, 0, "", ("127.0.0.1", 0)),
        ]

    monkeypatch.setattr("brand_ops.agents.web_fetch.socket.getaddrinfo", fake_getaddrinfo)
    assert _host_is_safe("mixed.example.com") is False


def test_unresolvable_host_is_unsafe(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket as _socket

    def fake_getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        raise _socket.gaierror("name or service not known")

    monkeypatch.setattr("brand_ops.agents.web_fetch.socket.getaddrinfo", fake_getaddrinfo)
    assert _host_is_safe("nope.invalid") is False


# The coroutine's gate short-circuits before any connection, so these stay offline.


def test_fetch_returns_none_for_blocked_targets() -> None:
    for url in (
        "http://127.0.0.1",
        "http://169.254.169.254",
        "http://10.0.0.1",
        "ftp://example.com",
        "",
    ):
        assert _run(fetch_page_text(url)) is None


# ─────────────────────────── HTML extraction ────────────────────────────────


_SAMPLE_HTML = """
<!doctype html>
<html>
  <head>
    <title>  Acme   Tools  </title>
    <meta name="description" content="Focused software for small teams.">
    <meta property="og:title" content="Acme (OG)">
    <meta property="og:description" content="OG description fallback.">
    <style>.brand { color: #ff0000; } body { margin: 0; }</style>
    <script>console.log("tracking pixel"); var x = 1;</script>
  </head>
  <body>
    <h1>Build calm software</h1>
    <p>Acme keeps the surface calm and the work fast.</p>
    <h2>For small teams</h2>
    <p>It serves teams who want to ship.</p>
    <noscript>Please enable JavaScript.</noscript>
    <script>document.title = "hijacked";</script>
  </body>
</html>
"""


def test_extractor_pulls_title_meta_headings_text() -> None:
    page = _page_extract_from_html(_SAMPLE_HTML, "https://acme.example.com/")
    assert isinstance(page, PageExtract)
    assert page.final_url == "https://acme.example.com/"
    # Title is collapsed (internal whitespace squeezed) and trimmed.
    assert page.title == "Acme Tools"
    assert page.meta_description == "Focused software for small teams."
    assert page.headings == ["Build calm software", "For small teams"]
    # Visible body text is present.
    assert "Acme keeps the surface calm and the work fast." in page.text
    assert "It serves teams who want to ship." in page.text


def test_extractor_strips_script_and_style() -> None:
    page = _page_extract_from_html(_SAMPLE_HTML, "https://acme.example.com/")
    # No CSS, no JS, and no <noscript> leakage into the body text.
    assert "color: #ff0000" not in page.text
    assert ".brand" not in page.text
    assert "tracking pixel" not in page.text
    assert "var x" not in page.text
    assert "document.title" not in page.text
    assert "hijacked" not in page.text
    assert "enable JavaScript" not in page.text


def test_extractor_og_fallback_when_no_plain_title() -> None:
    html = (
        "<html><head>"
        '<meta property="og:title" content="OG Only">'
        '<meta property="og:description" content="From OG.">'
        "</head><body><p>Body text here.</p></body></html>"
    )
    page = _page_extract_from_html(html, "https://x.example.com/")
    assert page.title == "OG Only"
    assert page.meta_description == "From OG."
    assert "Body text here." in page.text


def test_extractor_caps_text_to_max_chars() -> None:
    big = "word " * 10_000
    html = f"<html><body><p>{big}</p></body></html>"
    page = _page_extract_from_html(html, "https://x.example.com/", max_chars=500)
    assert len(page.text) <= 500
