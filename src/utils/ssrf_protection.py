import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import List, cast
from urllib.parse import urlparse

import aiohttp
from aiohttp.abc import AbstractResolver

logger = logging.getLogger(__name__)

_BLOCKED_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.goog",
})


class SSRFError(ValueError):
    pass


@dataclass(frozen=True)
class _ResolvedAddress:
    family: int
    proto: int
    flags: int
    host: str


class PinnedResolver(AbstractResolver):
    def __init__(self, addresses: List[_ResolvedAddress]):
        self._addresses = tuple(addresses)

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC):
        candidates = [
            addr
            for addr in self._addresses
            if family in (socket.AF_UNSPEC, addr.family)
        ]
        if not candidates:
            candidates = list(self._addresses)
        return [
            {
                "hostname": host,
                "host": addr.host,
                "port": port,
                "family": addr.family,
                "proto": addr.proto,
                "flags": addr.flags,
            }
            for addr in candidates
        ]

    async def close(self) -> None:
        return None


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_and_resolve(url: str) -> tuple[str, str, list[_ResolvedAddress]]:
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()

    if scheme not in ("http", "https"):
        raise SSRFError(f"Blocked scheme: {scheme!r} (only http/https allowed)")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFError(f"No hostname in URL: {url}")

    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise SSRFError(f"Blocked hostname: {hostname}")

    try:
        ip = ipaddress.ip_address(hostname)
        if _is_blocked_ip(ip):
            raise SSRFError(f"Blocked IP: {hostname} is a private/reserved address")
        return url, hostname, []
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise SSRFError(f"DNS resolution failed for {hostname}: {e}") from e

    if not infos:
        raise SSRFError(f"No DNS results for {hostname}")

    resolved: list[_ResolvedAddress] = []
    for family, _socktype, proto, _canonname, sockaddr in infos:
        ip_str = sockaddr[0]
        if not isinstance(ip_str, str):
            continue
        ip_text = cast(str, ip_str)
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise SSRFError(
                f"Blocked: {hostname} resolves to private/reserved IP {ip_text}"
            )
        resolved.append(
            _ResolvedAddress(
                family=family,
                proto=proto or socket.IPPROTO_TCP,
                flags=0,
                host=ip_text,
            )
        )

    if not resolved:
        raise SSRFError(f"No valid DNS results for {hostname}")

    return url, hostname, resolved


def validate_url(url: str) -> str:
    """Validate a URL is safe for outbound requests (not targeting internal networks).

    Resolves the hostname via DNS, then checks all returned IPs against
    RFC 1918 / RFC 3927 / loopback / link-local / reserved / multicast ranges.

    Args:
        url: The URL to validate.

    Returns:
        The validated URL (unchanged).

    Raises:
        SSRFError: If the URL targets a private/internal IP or a blocked hostname.
    """
    _validate_and_resolve(url)
    return url


def build_pinned_connector(url: str) -> tuple[str, aiohttp.TCPConnector]:
    validated_url, _hostname, addresses = _validate_and_resolve(url)
    if not addresses:
        return validated_url, aiohttp.TCPConnector(use_dns_cache=False, ttl_dns_cache=0)
    resolver = PinnedResolver(addresses)
    return validated_url, aiohttp.TCPConnector(
        resolver=resolver,
        use_dns_cache=False,
        ttl_dns_cache=0,
    )
