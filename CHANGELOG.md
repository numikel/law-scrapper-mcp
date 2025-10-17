# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2025-10-17

### Fixed
- Clarified keyword search logic in documentation - all keywords must be present (AND logic) instead of OR logic
- Added detailed notes about keyword search behavior in tool descriptions and examples
- Improved user guidance for multi-keyword searches

## [1.0.0] - 2025-01-17

### Added

#### 🕒 Dates and time utilities
- `get_current_date` - Get current date in YYYY-MM-DD format for legal document analysis
- `calculate_date_offset` - Calculate dates in the past or future by adding/subtracting time periods for legal document effective dates and deadlines

#### 📚 System metadata access
- `get_legal_keywords` - Retrieve all available keywords for categorizing Polish legal acts
- `get_legal_publishers` - Get list of all legal act publishers (Dziennik Ustaw, Monitor Polski) with metadata and publication years
- `get_publisher_details` - Get detailed information about a specific legal publisher including act counts and publication timeline
- `get_legal_statuses` - Get all possible legal act statuses (active, repealed, consolidated, etc.) for document classification
- `get_legal_types` - Retrieve all document types (laws, regulations, ordinances, etc.) used in Polish legal system
- `get_legal_institutions` - Get list of all institutions involved in Polish legal acts (ministries, authorities, organizations)

#### 🔍 Acts browsing and search
- `search_legal_acts` - Advanced search for Polish legal acts with multiple filters (date, type, keywords, publisher, status)
- `get_publisher_year_acts` - Get all legal acts published by a specific publisher in a given year

#### 📋 Act details and analysis
- `get_act_comprehensive_details` - Get complete detailed information about a specific legal act including metadata, status, dates, and references
- `get_act_content` - Retrieve the actual text content of a legal act in PDF or HTML format
- `get_act_table_of_contents` - Get the hierarchical structure and table of contents of a legal act
- `get_act_relationships` - Analyze legal relationships and references for an act (amendments, references, etc.)

### Features
- **Comprehensive legal act access** - Full access to Polish legal acts from Dziennik Ustaw and Monitor Polski
- **Advanced search & filtering** - Multi-criteria search by date, type, keywords, publisher, and status
- **Detailed document analysis** - Complete metadata, structure, references, and content retrieval
- **Date & time utilities** - Specialized date calculations for legal document analysis
- **System metadata access** - Keywords, statuses, document types, and institution data
- **FastMCP integration** - Built with FastMCP framework following best practices
- **Professional documentation** - Extensive examples and clear parameter descriptions
- **RESTful API integration** - Direct connection to official Sejm API endpoints

### Technical
- Initial release with 14 specialized tools organized in 4 categories
- FastMCP framework implementation
- Comprehensive error handling and logging
- Professional code documentation with detailed docstrings
- MCP server configuration for Cursor IDE, Claude Code, and other MCP-supported applications

### Dependencies
- fastmcp>=2.12.4
- logging>=0.4.9.6
- python-dateutil>=2.9.0
- requests>=2.32.5

### Authors
- [@numikel](https://github.com/numikel)
