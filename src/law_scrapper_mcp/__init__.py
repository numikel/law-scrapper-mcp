"""Law Scrapper MCP - Polish legal acts research server."""

from importlib.metadata import PackageNotFoundError, version

# Read from the installed distribution rather than hardcoded: a literal here sat
# outside the release script's sync set and stayed at 3.0.0 for five releases.
try:
    __version__ = version("law-scrapper-mcp")
except PackageNotFoundError:  # a checkout that was never installed has no honest version to claim
    __version__ = "0.0.0+unknown"
