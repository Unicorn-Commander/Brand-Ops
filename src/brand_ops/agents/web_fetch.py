"""Best-effort live-site capture for brand intake (no Brigade required).

The new-brand "Research" stage used to do no real research without Brigade: the
intake agent passed the raw URL to the LLM as a hint and noted that a live
capture "needs Brigade connected". This module closes that gap with a small,
self-contained fetcher that pulls the readable text off a public web page so the
draft is grounded in what the site actually says, while Brigade stays a future
augmentation for deeper research.

The single entry point is ``fetch_page_text(url)``. It is best-effort by design:
it returns a ``PageExtract`` on success and ``None`` on any problem (bad scheme,
a blocked target, a non-HTML response, a transport error, a parse failure). It
never raises into the intake flow.

Security: this fetcher takes a URL that ultimately comes from an operator, so it
guards against SSRF. Only http/https are allowed, and the resolved host must not
be a private, loopback, link-local, or otherwise reserved address (which would
let a crafted URL reach internal services or the cloud metadata endpoint at
169.254.169.254). Resolution and classification use the stdlib ``socket`` and
``ipaddress`` only.

httpx is a hard dependency of this package but is lazy-imported inside the fetch
so importing this module never pulls in the http stack and never touches the
network, matching ``brigade/client.py``.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse

# A descriptive desktop-browser User-Agent. The suite has hit Cloudflare bot
# blocks with default/library agents (the openai-SDK "OpenAI/Python" UA, urllib),
# so we present as a real browser by default. Overridable via the setting on
# config.py (env INTAKE_FETCH_USER_AGENT); held here as the fallback so this
# module works even if config is not threaded through.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 BrandOps/0.3 "
    "(+https://unicorncommander.ai; brand intake site capture)"
)

# Content types we will parse. Anything else (PDF, image, JSON, octet-stream) is
# not readable brand copy, so we decline rather than feed bytes to the parser.
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

# How many headings to keep. A page can carry dozens; a handful is plenty of
# signal for the draft and keeps the captured context small.
_MAX_HEADINGS = 12


@dataclass
class PageExtract:
    """The readable signal pulled off a fetched page. All fields best-effort.

    ``final_url`` is the URL after redirects, ``title``/``meta_description`` come
    from the document head (with Open Graph as a fallback), ``headings`` are the
    visible h1/h2 text in document order, and ``text`` is the collapsed visible
    body text capped to the caller's ``max_chars``.
    """

    final_url: str
    title: str = ""
    meta_description: str = ""
    headings: list[str] = field(default_factory=list)
    text: str = ""


def _user_agent() -> str:
    """The configured intake User-Agent, falling back to the module default.

    Read lazily from settings so a deploy can override it (Cloudflare tuning)
    without this module hard-importing config at module load. Any failure to
    read the setting falls back to the descriptive default.
    """
    try:
        from ..config import get_settings

        ua = getattr(get_settings(), "intake_fetch_user_agent", "") or ""
        return ua.strip() or _DEFAULT_USER_AGENT
    except Exception:
        return _DEFAULT_USER_AGENT


def _is_blocked_ip(ip: str) -> bool:
    """True when an already-resolved IP literal is an unsafe SSRF target.

    Blocks private (RFC-1918 10/8, 172.16/12, 192.168/16), loopback
    (127.0.0.0/8, ::1), link-local (169.254.0.0/16 incl. the 169.254.169.254
    metadata endpoint, fe80::/10), reserved, multicast, and the unspecified
    0.0.0.0 / ::. ``is_private`` already covers the RFC-1918 and unique-local
    (fc00::/7) ranges; the rest are checked explicitly for clarity and so a
    future Python widening one category cannot silently open a hole.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        # Not a parseable address: treat as unsafe rather than guess.
        return True
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _host_is_safe(host: str) -> bool:
    """Resolve ``host`` and return True only if every address is a safe target.

    A host can resolve to several addresses (A and AAAA, round-robin). We block
    if ANY resolved address is unsafe so a hostname cannot smuggle an internal
    address alongside a public one. A host that does not resolve is unsafe.
    """
    if not host:
        return False
    # A bare IP literal in the URL: classify it directly (also strips an IPv6
    # literal's surrounding brackets, which getaddrinfo would otherwise reject).
    literal = host.strip("[]")
    try:
        ipaddress.ip_address(literal)
        return not _is_blocked_ip(literal)
    except ValueError:
        pass  # Not a literal; fall through to DNS resolution.

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not infos:
        return False
    for info in infos:
        sockaddr = info[4]
        ip = sockaddr[0]
        if _is_blocked_ip(ip):
            return False
    return True


def is_url_fetchable(url: str) -> bool:
    """True when ``url`` is http/https and resolves to a safe (non-internal) host.

    The SSRF gate, exposed as the package's reusable guard: the image backends
    route a provider-returned image URL through this before fetching it, so a
    compromised upstream cannot redirect a fetch at an internal service or the
    cloud metadata endpoint. Rejects any non-http(s) scheme up front, then defers
    to ``_host_is_safe`` for the address classification.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return _host_is_safe(parsed.hostname or "")


# Back-compat alias: the original private name kept so existing imports and tests
# that reference ``_url_is_fetchable`` keep resolving to the same guard.
_url_is_fetchable = is_url_fetchable


class _TextExtractor(HTMLParser):
    """Collect a page's visible text, title, description, and h1/h2 headings.

    Drops the content of <script>, <style>, <noscript>, and <template> so
    generated/styling text never lands in the body. Captures the document
    <title>, <meta name=description> and Open Graph og:title / og:description,
    and visible h1/h2 text in order. Whitespace is left raw here and collapsed by
    the caller. (<head> is NOT skipped wholesale because <title> and <meta> live
    inside it; the head's own <script>/<style> are dropped by their tags, and
    head data that is neither title nor a heading simply has no visible parent.)
    """

    # Tags whose text content is never visible copy.
    _SKIP_CONTENT = {"script", "style", "noscript", "template"}
    _HEADING_TAGS = {"h1", "h2"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str = ""
        self.meta_description: str = ""
        self._og_title: str = ""
        self._og_description: str = ""
        self.headings: list[str] = []
        self._text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []

    # -- meta resolution ------------------------------------------------------

    @property
    def resolved_title(self) -> str:
        """The <title>, or og:title when the document had no plain title."""
        return self.title or self._og_title

    @property
    def resolved_description(self) -> str:
        """meta description, or og:description as a fallback."""
        return self.meta_description or self._og_description

    # -- parser callbacks -----------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_CONTENT:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            self._handle_meta(dict(attrs))
            return
        if tag in self._HEADING_TAGS and self._skip_depth == 0:
            self._heading_tag = tag
            self._heading_parts = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Void/self-closed meta still carries description/og data.
        if tag == "meta":
            self._handle_meta(dict(attrs))

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_CONTENT:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in self._HEADING_TAGS and self._heading_tag == tag:
            heading = " ".join(self._heading_parts).strip()
            if heading and len(self.headings) < _MAX_HEADINGS:
                self.headings.append(heading)
            self._heading_tag = None
            self._heading_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title += data
            return
        if self._heading_tag is not None:
            self._heading_parts.append(data)
        # Headings are also part of the visible body, so collect either way.
        self._text_parts.append(data)

    # -- internals ------------------------------------------------------------

    def _handle_meta(self, attrs: dict[str, str | None]) -> None:
        """Pull description / og:title / og:description out of a <meta> tag."""
        name = (attrs.get("name") or "").strip().lower()
        prop = (attrs.get("property") or "").strip().lower()
        content = (attrs.get("content") or "").strip()
        if not content:
            return
        if name == "description" and not self.meta_description:
            self.meta_description = content
        elif prop == "og:title" and not self._og_title:
            self._og_title = content
        elif prop == "og:description" and not self._og_description:
            self._og_description = content


def _collapse(text: str) -> str:
    """Collapse runs of whitespace (incl. newlines) into single spaces."""
    return " ".join(text.split())


def _page_extract_from_html(
    html_text: str, final_url: str, *, max_chars: int = 12_000
) -> PageExtract:
    """Parse an HTML string into a PageExtract. Pure: no network, never raises.

    Split out from ``fetch_page_text`` so the parser is unit-testable on its own.
    Drops <script>/<style> content, collapses whitespace, and caps the body text
    to ``max_chars``. A parse failure yields an empty extract (so the fetch path
    can still return a best-effort result rather than raising).
    """
    parser = _TextExtractor()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        return PageExtract(final_url=final_url)
    return PageExtract(
        final_url=final_url,
        title=_collapse(parser.resolved_title),
        meta_description=_collapse(parser.resolved_description),
        headings=parser.headings,
        text=_collapse(" ".join(parser._text_parts))[:max_chars],
    )


async def fetch_page_text(
    url: str,
    *,
    timeout: float = 15.0,
    max_bytes: int = 2_000_000,
    max_chars: int = 12_000,
) -> PageExtract | None:
    """Fetch ``url`` and return its readable text, or ``None`` on any problem.

    Best-effort and side-effect free: GETs the URL following redirects with a
    descriptive desktop User-Agent, enforces the SSRF gate (http/https only and
    a non-internal resolved host) before connecting, declines non-HTML responses,
    caps the body at ``max_bytes`` and the extracted text at ``max_chars``, and
    parses the HTML for title, meta description, h1/h2 headings, and visible body
    text. Returns a ``PageExtract`` on success and ``None`` for a blocked target,
    a non-HTML response, a transport error, or a parse failure. Never raises.
    """
    if not url or not url.strip():
        return None
    url = url.strip()
    # SSRF gate: validate scheme and the initial host before any connection.
    if not is_url_fetchable(url):
        return None

    import httpx

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={
                "User-Agent": _user_agent(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            async with client.stream("GET", url) as response:
                # Re-validate the final host after redirects: a public URL can
                # 30x to an internal target, which the initial check would miss.
                final_url = str(response.url)
                if not is_url_fetchable(final_url):
                    return None
                if response.status_code >= 400:
                    return None
                content_type = response.headers.get("content-type", "")
                media_type = content_type.split(";", 1)[0].strip().lower()
                if media_type and media_type not in _HTML_CONTENT_TYPES:
                    return None

                # Read the body up to the cap, then stop; we do not need a whole
                # multi-megabyte page to draft from.
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= max_bytes:
                        break
                raw = b"".join(chunks)[:max_bytes]
                encoding = response.encoding or "utf-8"
    except httpx.HTTPError:
        return None
    except Exception:
        # Best-effort: any unexpected failure means "no capture", never a raise.
        return None

    try:
        html_text = raw.decode(encoding, errors="replace")
    except (LookupError, ValueError):
        html_text = raw.decode("utf-8", errors="replace")

    return _page_extract_from_html(html_text, final_url, max_chars=max_chars)
