# law-scrapper-mcp-voivodeship-journals

A source module for Law Scrapper MCP that serves acts of the voivodeship official journals
(e-Dziennik ELI) through the existing tools. It adds no tool: `search_legal_acts`, `browse_acts`,
`track_legal_changes`, `get_act_details`, `read_act_content`, `search_in_act`,
`analyze_act_relationships` and `compare_acts` accept the publisher codes `POL_WOJ_*`.

## Use in this repository

```bash
# The module is a uv workspace member: plain `uv sync --extra dev` does NOT install it
uv sync --extra dev --all-packages

# Module tests (against the installed distribution)
.venv/Scripts/python.exe -m pytest modules/voivodeship-journals/tests -v
```

Every installed module is active unless switched off. `LAW_MCP_SOURCE_MODULES=-voivodeship-journals`
switches it off (the package is then never imported); `-*,voivodeship-journals` runs this module
only.

The served codes, the withheld ones and the exact refusals are documented in the server
instructions the module contributes (`instructions.py`).
