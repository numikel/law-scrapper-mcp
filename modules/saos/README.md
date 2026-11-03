# law-scrapper-mcp-saos

Source module for Law Scrapper MCP: search Polish court judgments in SAOS (`www.saos.org.pl`).

The full description (tools, limits, data quirks, how to switch the module off) is written together
with the last tool of the module.

```bash
# The module is a uv workspace member: plain `uv sync --extra dev` does NOT install it
uv sync --extra dev --all-packages

.venv/Scripts/python.exe -m pytest modules/saos/tests -v
```
