"""Dependency-free leaves shared by `config.py` and `config_validation.py`.

Split out (Task 6 fix round 1, Klaster 7 cleanup) because both modules need
`MIN_AUTH_TOKEN_BYTES` / `is_loopback_entry`, and having either module import
them from the other created a real circular import: `config.py` runs
`settings = Settings()` at module scope, which triggers `Settings`'s
security-boundary validator during `config.py`'s own import — so any import
edge back into `config.py` from a module `config.py` also imports is live,
not just theoretical. This module imports nothing from either, so it cannot
participate in a cycle.
"""

from __future__ import annotations

from ipaddress import ip_address

MIN_AUTH_TOKEN_BYTES = 32


def _host_of(entry: str) -> str:
    """Strip scheme, port and brackets from an allowlist entry."""
    value = entry.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    # An allowlist entry is an authority. The SDK compares `Host`/`Origin` values
    # verbatim and neither ever carries a path, so anything from the first `/`
    # on can only be a trailing slash someone typed; drop it before classifying,
    # for the bracketed and the hostname forms alike.
    value = value.split("/", 1)[0]
    if value.startswith("["):
        if "]" not in value:
            return value[1:]
        inner, _, suffix = value[1:].partition("]")
        # After the closing bracket only a port (`:8080`), the SDK's wildcard
        # port (`:*`) or nothing may follow. `[::1].evil.com` is a hostname
        # the SDK compares verbatim, so it must never resolve to the loopback
        # literal inside the brackets — return it raw and let `ip_address`
        # reject it.
        if suffix == "" or suffix == ":*" or (suffix.startswith(":") and suffix[1:].isdigit()):
            return inner
        return value
    # Bare IPv6 (contains multiple colons but no brackets): return unchanged
    if value.count(":") > 1:
        return value
    return value.split(":")[0]


def is_loopback_entry(entry: str) -> bool:
    """Whether an allowlist entry or bind address stays inside the loopback."""
    host = _host_of(entry).lower()
    if host in {"localhost", "::1"}:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False
