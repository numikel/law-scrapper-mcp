# Law Scrapper MCP 📜⚖️

A comprehensive Model Context Protocol (MCP) server for accessing and analyzing Polish legal acts from the Sejm API, enabling AI-powered legal research and document analysis.

![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Version](https://img.shields.io/badge/version-1.0.1-orange.svg)

## ✨ Features

✅ **Comprehensive legal act access** - Full access to Polish legal acts from Dziennik Ustaw and Monitor Polski
✅ **Advanced search & filtering** - Multi-criteria search by date, type, keywords, publisher, and status
✅ **Detailed document analysis** - Complete metadata, structure, references, and content retrieval
✅ **Date & time utilities** - Specialized date calculations for legal document analysis
✅ **System metadata access** - Keywords, statuses, document types, and institution data
✅ **FastMCP integration** - Built with FastMCP framework following best practices
✅ **Professional documentation** - Extensive examples and clear parameter descriptions
✅ **RESTful API integration** - Direct connection to official Sejm API endpoints

## 📋 Requirements / prerequisites

- **Python**: 3.12 or higher
- **Package manager**: uv (recommended) or pip
- **Internet connection**: Required for accessing Sejm API endpoints
- **MCP-compatible tool**: Cursor IDE, Claude Code, or other MCP-supported applications

## 🚀 Installation

### Using uv (recommended)

```bash
# Clone the repository
git clone https://github.com/numikel/law-scrapper-mcp.git
cd law-scrapper-mcp

# Install dependencies
uv sync
```

### Using pip

```bash
# Clone the repository
git clone https://github.com/numikel/law-scrapper-mcp.git
cd law-scrapper-mcp

# Install dependencies
pip install -e .
```

## ⚙️ Configuration

### MCP server configuration

Add the following configuration to your MCP client's configuration file:

#### For Cursor IDE (`.cursor/mcp.json`):
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

#### For Claude Code:
```bash
claude mcp add law-scrapper-mcp uvx '--from' 'git+https://github.com/numikel/law-scrapper-mcp' 'law-scrapper'
```

#### For other MCP tools (`.mcp.json` or `mcp_config.json`):
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

## 🎯 Quick start / usage

Once configured, you can start using the legal research tools:

### Basic usage examples

**Get current date for legal analysis:**
> "What is today's date?"

**Search for specific legal acts:**
> "Find all regulations from 2020 containing 'environment'"

**Analyze document structure:**
> "Show me the table of contents for act DU/2020/1"

**Get comprehensive document details:**
> "Give me full information about legal act DU/2020/1280"

### Available tool categories

The server provides 14 specialized tools organized in 4 categories:

#### 🕒 **Dates & time** (2 tools)
- `get_current_date` - Get current date for legal analysis
- `calculate_date_offset` - Calculate date ranges for legal periods

#### 📚 **System metadata** (6 tools)
- `get_legal_keywords` - Access all available search keywords
- `get_legal_publishers` - List all legal publishers (DU, MP)
- `get_publisher_details` - Detailed publisher information
- `get_legal_statuses` - Document status types
- `get_legal_types` - Legal document types
- `get_legal_institutions` - Involved institutions

#### 🔍 **Acts browsing & search** (2 tools)
- `search_legal_acts` - Advanced multi-criteria search
- `get_publisher_year_acts` - Browse acts by publisher and year

#### 📋 **Act details & analysis** (4 tools)
- `get_act_comprehensive_details` - Complete document metadata
- `get_act_content` - PDF/HTML content retrieval
- `get_act_table_of_contents` - Document structure analysis
- `get_act_relationships` - Legal references and amendments

## 📁 Project structure

```
law-scrapper-mcp/
├── app.py                    # Main MCP server implementation with 14 legal research tools
├── pyproject.toml           # Project configuration, dependencies, and CLI scripts
├── uv.lock                  # Lock file ensuring reproducible builds
├── README.md               # Project documentation
└── __pycache__/            # Python bytecode cache (generated)
```

## 🛠️ Development

### Development setup

```bash
# Install in development mode
uv sync

# Run the server directly
uv run app.py

# Or use the installed script
law-scrapper
```

### Running tests

```bash
# Run basic functionality tests
uv run python -c "
import app
print('Server imports successfully')
funcs = [name for name in dir(app) if name.startswith('get_')]
print(f'Available tools: {len(funcs)}')
"
```

### Code quality

The project follows FastMCP best practices:
- **Tagged Tools**: All tools have descriptive tags for filtering
- **Annotated Parameters**: Every parameter has clear descriptions
- **Comprehensive Examples**: Minimum 5 examples per tool
- **Professional Documentation**: Detailed docstrings and usage examples

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes using Conventional Commits format
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development guidelines

- Follow FastMCP best practices for tool definitions
- Include comprehensive examples and parameter descriptions
- Add appropriate tags for tool categorization
- Test all new functionality before submitting
- Use English for all code comments and documentation

## 📄 License

This project is licensed under the MIT License

## 👤 Author

**[@numikel](https://github.com/numikel)**

---

**Legal disclaimer**: This tool provides access to Polish legal documents for research purposes. Always consult with qualified legal professionals for legal advice and interpretation of laws.