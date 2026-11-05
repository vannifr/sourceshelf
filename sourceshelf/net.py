import ipaddress
import os
import socket
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

VERSION = "0.1"
USER_AGENT = f"sourceshelf/{VERSION}"
MAX_BYTES = 50 * 1024 * 1024
ALLOWED_SCHEMES = ('http', 'https')
PAUSES = {"export.arxiv.org": 3.0, "arxiv.org": 15.0}
ROBOTS_EXEMPT = ("export.arxiv.org",)


class NetError(Exception):
    pass


@dataclass
class Response:
    status: int
    content_type: str
    content: bytes


NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _embedded_v4(addr):
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return addr.ipv4_mapped
        if addr.sixtofour:
            return addr.sixtofour
        if addr in NAT64:
            return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return addr


def check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES or not parts.hostname:
        raise NetError(f"only http and https URLs are allowed: {url}")
    if os.environ.get("KB_ALLOW_PRIVATE") == "1":
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as error:
        raise NetError(f"cannot resolve {parts.hostname}: {error}") from error
    for info in infos:
        addr = _embedded_v4(ipaddress.ip_address(info[4][0].split("%")[0]))
        if not addr.is_global:
            raise NetError(f"refusing non-public address {addr} for {parts.hostname} (set KB_ALLOW_PRIVATE=1 to allow)")


class _CheckedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urlsplit(newurl).netloc != urlsplit(req.full_url).netloc:
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


def safe_open(req, timeout=60):
    check_url(req.full_url)
    return build_opener(_CheckedRedirect).open(req, timeout=timeout)


class Net:
    def __init__(self, opener=safe_open, sleep_fn=time.sleep, clock=time.monotonic):
        self.opener, self.sleep_fn, self.clock = opener, sleep_fn, clock
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.last: dict[str, float] = {}

    def _fetch(self, url: str) -> Response:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self.opener(req, timeout=60) as r:
                content = r.read(MAX_BYTES + 1)
                if len(content) > MAX_BYTES:
                    raise NetError(f"response from {url} larger than {MAX_BYTES // (1024 * 1024)} MB")
                return Response(r.getcode(), r.headers.get("Content-Type", ""), content)
        except HTTPError as error:
            return Response(error.code, "", b"")
        except NetError:
            raise
        except (URLError, TimeoutError, OSError) as error:
            raise NetError(f"network error at {url}: {error}") from error

    def _pause(self, host: str) -> None:
        wait = PAUSES.get(host, 0.0)
        if wait and host in self.last:
            rest = wait - (self.clock() - self.last[host])
            if rest > 0:
                self.sleep_fn(rest)
        self.last[host] = self.clock()

    def _allowed(self, url: str) -> bool:
        d = urlsplit(url)
        base = f"{d.scheme}://{d.netloc}"
        if base not in self.robots:
            resp = self._fetch(f"{base}/robots.txt")
            if resp.status == 200:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(resp.content.decode("utf-8", errors="ignore").splitlines())
                self.robots[base] = rp
            else:
                self.robots[base] = None
        rp = self.robots[base]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def __call__(self, url: str) -> Response:
        host = urlsplit(url).netloc
        if host not in ROBOTS_EXEMPT and not self._allowed(url):
            raise NetError(f"robots.txt forbids {url}")
        self._pause(host)
        return self._fetch(url)
