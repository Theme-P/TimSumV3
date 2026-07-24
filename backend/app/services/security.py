"""Shared security helpers for authentication and request attribution.

The helpers in this module deliberately do not trust request ``Host``,
``Origin`` or forwarded headers by default.  Public links are built from an
operator-controlled origin and forwarded client addresses are accepted only
from explicitly trusted reverse proxies.
"""

from __future__ import annotations

import ipaddress
import os
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlsplit, urlunsplit

if TYPE_CHECKING:
    from fastapi import Request


MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024


class SecurityConfigurationError(RuntimeError):
    """Raised when a security-sensitive setting is missing or invalid."""


def validate_password(password: str, *, reject_bootstrap_defaults: bool = False) -> str:
    """Validate a new password without silently normalizing it.

    Bootstrap credentials receive an additional check that rejects the naming
    pattern used by the credentials that were previously published with the
    project.  The actual leaked credentials are intentionally not retained as
    usable constants in source control.
    """
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"รหัสผ่านต้องมีอย่างน้อย {MIN_PASSWORD_LENGTH} ตัวอักษร"
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"รหัสผ่านต้องมีความยาวไม่เกิน {MAX_PASSWORD_LENGTH} ตัวอักษร"
        )

    if reject_bootstrap_defaults:
        normalized = password.casefold()
        if "timsum" in normalized and "admin" in normalized:
            raise ValueError("ห้ามใช้รหัสผ่าน bootstrap เริ่มต้นที่เคยเผยแพร่")

    return password


def get_public_frontend_url() -> str:
    """Return the configured public frontend origin.

    This is intentionally environment-only.  Request-controlled ``Host`` and
    ``Origin`` values must never influence password-reset or login links.
    """
    raw_url = os.getenv("PUBLIC_FRONTEND_URL", "").strip()
    if not raw_url:
        raise SecurityConfigurationError("PUBLIC_FRONTEND_URL is not configured")

    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise SecurityConfigurationError(
            "PUBLIC_FRONTEND_URL must be an absolute HTTP(S) URL"
        ) from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise SecurityConfigurationError(
            "PUBLIC_FRONTEND_URL must be an absolute HTTP(S) URL"
        )
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise SecurityConfigurationError(
            "PUBLIC_FRONTEND_URL contains an invalid port"
        ) from exc
    if parsed_port is not None and not 1 <= parsed_port <= 65535:
        raise SecurityConfigurationError(
            "PUBLIC_FRONTEND_URL contains an invalid port"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SecurityConfigurationError(
            "PUBLIC_FRONTEND_URL must not contain credentials, query, or fragment"
        )

    if os.getenv("APP_ENV", "").strip().lower() == "production" and parsed.scheme != "https":
        raise SecurityConfigurationError(
            "PUBLIC_FRONTEND_URL must use HTTPS in production"
        )

    normalized_path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def build_frontend_url(path: str, query: dict[str, str] | None = None) -> str:
    """Build a frontend URL from the trusted configured public origin."""
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Frontend link path must be absolute and local")

    url = f"{get_public_frontend_url()}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    return url


def _trusted_proxy_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    raw_cidrs = os.getenv("TRUSTED_PROXY_CIDRS", "")
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for raw_cidr in raw_cidrs.split(","):
        cidr = raw_cidr.strip()
        if not cidr:
            continue
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            # Invalid configuration must not make an untrusted peer trusted.
            continue
    return tuple(networks)


def get_client_ip(request: "Request") -> str:
    """Return a validated client IP, trusting forwarding only from allowlisted proxies."""
    peer = request.client.host if request.client else ""
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return "unknown"

    if not any(peer_ip in network for network in _trusted_proxy_networks()):
        return str(peer_ip)

    forwarded = request.headers.get("x-forwarded-for", "")
    candidate = forwarded.split(",", 1)[0].strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return str(peer_ip)
