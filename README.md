# Law Scrapper MCP 📜⚖️

Law Scrapper MCP is a Model Context Protocol (MCP) server for monitoring and fetching Polish law acts published by the Sejm, with intelligent topic filtering and AI-powered content summarization.

## ✨ Features

✅ **Polish Law Monitoring** - Fetch and monitor law acts published by the Polish Sejm
✅ **Topic-based Filtering** - Filter law acts by specific keywords and topics
✅ **AI Summarization** - Use LLM integration to summarize legal content
✅ **MCP Integration** - Built on FastMCP framework for seamless integration with MCP-compatible AI applications
✅ **RESTful API Access** - Direct integration with Sejm's official API endpoints

## Quick Start

1. **Install MCP server in your AI tool**

   👉 Guidelines for most popular AI Tools below

2. **Ask your AI assistant:**

   > "Please fetch Polish law keywords with available tools"

## Installation & setup

- [Cursor IDE](#cursor-ide)
- [Claude Code](#claude-code)
- [Other tools](#other-tools)

### Cursor IDE

Add the following configuration to your `.cursor/mcp.json` file:

```json
{
  "mcpServers": {
    "law-scrapper-mcp": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/numikel/law-scrapper-mcp",
        "law-scrapper"
      ],
      "transport": "stdio"
    }
  }
}
```

### Claude Code

**Run the following command in your project directory:**

```
claude mcp add law-scrapper-mcp uvx '--from' 'git+https://github.com/numikel/law-scrapper-mcp' 'law-scrapper'
```

You can alternatively create `.mcp.json` file in your project directory with the following content:

```json
{
  "mcpServers": {
    "law-scrapper-mcp": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/numikel/law-scrapper-mcp",
        "law-scrapper"
      ],
      "transport": "stdio"
    }
  }
}
```

### Other tools

For other MCP-compatible tools, create an `mcp_config.json` file in your project's root directory:

```json
{
  "mcpServers": {
    "law-scrapper-mcp": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/numikel/law-scrapper-mcp",
        "law-scrapper"
      ],
      "transport": "stdio"
    }
  }
}
```

## Usage

Once installed and configured, you can invoke the tool by asking your AI assistant:

> "Please fetch Polish law keywords with available tools"

## Output Format

The tool returns a list of keywords from the Sejm API:

```json
[
  "prawo pracy",
  "podatki",
  "ochrona środowiska",
  "zdrowie publiczne"
]
```

## Development

### Prerequisites

```bash
# Python 3.12 or higher
# Package Manager: uv
# Internet Connection: Required for accessing Sejm API endpoints
```

### Development mode

```bash
# Using uv
uv run app.py

# Or using the installed script
law-scrapper
```

## 📁 Project structure

```
law-scrapper-mcp/
├── app.py              # Main MCP server implementation with Polish law monitoring tools
├── pyproject.toml      # Project configuration, dependencies, and CLI script definition
├── uv.lock             # Lock file for uv package manager ensuring reproducible builds
└── README.md           # Project documentation
```

## 🛠️ Development

### Development commands

```bash
# Run the server in development mode
uv run app.py

# Or use the script
law-scrapper
```

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License.

## Author

[@numikel](https://github.com/numikel)
