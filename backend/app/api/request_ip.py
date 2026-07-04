"""Client-address helpers for the write-guard and the per-IP rate limiter.

The backend publishes no host port in the docker deployment: every request
arrives either from inside its own container (a loopback ``docker exec`` client)
or from the nginx container (the trusted reverse proxy). ``TRUSTED_PROXY_IPS``
names the proxy's network so we can tell those apart.

Two distinct questions are answered here:

* *Rate limiting* wants a spoof-resistant client key. Behind the proxy that is
  the **rightmost** ``X-Forwarded-For`` entry — the address nginx actually saw
  (nginx appends it via ``$proxy_add_x_forwarded_for``). The leftmost entries are
  client-supplied and forgeable, so we never key on them.
* *The write-guard* (see ``guards.require_local_write``) does not trust any
  forwarded IP to look like loopback. Instead it allows proxied writes only when
  the dashboard is bound host-only (``proxy_is_host_only``), because then the LAN
  cannot reach the proxy at all and every forwarded request is from the host.
"""

from __future__ import annotations

from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from fastapi import Request

from app.config import get_settings

# ip_network accepts a bare IP ("127.0.0.1" -> /32) or a CIDR ("172.28.0.0/16").
Network = IPv4Network | IPv6Network


@lru_cache(maxsize=8)
def _parse_trusted(raw: str) -> tuple[Network, ...]:
    """Parse the comma-separated TRUSTED_PROXY_IPS value into networks.

    Cached on the raw string so we don't re-parse on every request; the tiny
    cache turns over only when the setting itself changes (tests, reload).
    Unparseable entries are skipped rather than crashing request handling.
    """
    networks: list[Network] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ip_network(part, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def direct_peer(request: Request) -> str | None:
    """The immediate TCP peer's address (the proxy, or a loopback exec client)."""
    return request.client.host if request.client else None


def is_trusted_proxy(ip: str | None) -> bool:
    """True when ``ip`` falls inside a configured TRUSTED_PROXY_IPS network."""
    if not ip:
        return False
    networks = _parse_trusted(get_settings().trusted_proxy_ips)
    if not networks:
        return False
    try:
        addr = ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def proxy_is_host_only() -> bool:
    """True when the dashboard is published host-only.

    Derived from ``FRONTEND_BIND`` (the same value that binds the published port
    in docker-compose). When it's a loopback address the LAN can't reach the
    proxy, so any request the proxy forwards necessarily comes from the host.
    """
    bind = get_settings().frontend_bind.strip()
    if bind == "localhost":
        return True
    try:
        return ip_address(bind).is_loopback
    except ValueError:
        return False


def resolve_client_ip(request: Request) -> str | None:
    """Spoof-resistant client key for rate limiting.

    Behind a trusted proxy, return the rightmost X-Forwarded-For entry (the
    address nginx observed); otherwise the direct peer. Never keys on the
    forgeable leftmost entries.
    """
    peer = direct_peer(request)
    if not is_trusted_proxy(peer):
        return peer

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        entries = [part.strip() for part in forwarded.split(",") if part.strip()]
        if entries:
            return entries[-1]
    return peer
