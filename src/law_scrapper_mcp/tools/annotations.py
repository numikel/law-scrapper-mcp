"""Tool annotation presets shared by every MCP tool in this package.

Annotations are hints for the host, not enforcement: the MCP specification tells
clients not to trust them from an untrusted server, so they carry no security
value here. Their value is information for the host and its approval UX.

Every tool is read-only. The only state a call touches is this process's
memory (ResultStore, DocumentStore, the TTL cache); it dies with the process and
changes nothing outside the server, so it is not the "environment" that
`readOnlyHint` describes. That reading holds only while those stores stay in
process memory. Once any of them becomes durable (disk, Redis, a shared cache),
`readOnlyHint` and `idempotentHint` must be decided again for the tools that
create state: search_legal_acts, browse_acts, filter_results,
track_legal_changes and get_act_details.

`destructiveHint` and `idempotentHint` only matter when `readOnlyHint` is false,
but both are spelled out: their protocol defaults are pessimistic, and a client
that skips the `readOnlyHint` check would otherwise read every tool as
destructive.

`openWorldHint` marks the tools that call api.sejm.gov.pl.

A tool that writes anything must not borrow these presets; it needs its own
annotations, chosen on purpose. tests/unit/test_tool_architecture.py forces
every tool to name one explicitly.
"""

from mcp.types import ToolAnnotations

READ_ONLY_REMOTE = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
"""Read-only tool that calls api.sejm.gov.pl."""

READ_ONLY_LOCAL = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
"""Read-only tool that never leaves the process."""
