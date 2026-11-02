# law-scrapper-mcp-reference

A demonstration source module for Law Scrapper MCP. **It is never released and never part of the
container image.** It exists to prove, against a real entry point and a real STDIO server, that the
core can discover a module, count its tools and switch it off, and to be the template a new module
copies.

It adds one tool, `reference_ping`, which makes no network call: it reports the module name and
version and that the start hook ran.

## Use in this repository

```bash
# The module is a uv workspace member: plain `uv sync --extra dev` does NOT install it
uv sync --extra dev --all-packages

# Module tests (real installed distribution, real STDIO server)
.venv/Scripts/python.exe -m pytest modules/reference/tests -v
```

Switch it off with `LAW_MCP_SOURCE_MODULES=-reference`; a switched-off module is never imported.
`LAW_MCP_SOURCE_MODULES=-*` runs no module, and `-*,reference` runs only this one (tokens apply left
to right, the last token about a module wins).
