# law-scrapper-mcp-saos

Source module for Law Scrapper MCP: search Polish court judgments in SAOS (`www.saos.org.pl`).

The full description (tools, limits, data quirks, how to switch the module off) is written together
with the last tool of the module.

**Host and pace.** The module talks to one host, `www.saos.org.pl`, and asks it for at most 2 requests
per second with a burst of 4 (lower than the core's default, because SAOS is a public academic service
and a page of 100 records is about 140 kB). Every `phrase` and `signature` you search for is sent to
that host.

```bash
# The module is a uv workspace member: plain `uv sync --extra dev` does NOT install it
uv sync --extra dev --all-packages

.venv/Scripts/python.exe -m pytest modules/saos/tests -v
```
