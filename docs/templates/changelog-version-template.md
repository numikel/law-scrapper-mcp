# Changelog - vX.Y.Z

All notable changes to the `law-scrapper-mcp` project for version vX.Y.Z will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [vX.Y.Z] - YYYY-MM-DD

### Install

The release attaches the core and every source module as wheels, all version X.Y.Z; a module requires
the core of exactly its own version.

```bash
RELEASE=https://github.com/numikel/law-scrapper-mcp/releases/download/vX.Y.Z
pip install "$RELEASE/law_scrapper_mcp-X.Y.Z-py3-none-any.whl" \
            "$RELEASE/law_scrapper_mcp_voivodeship_journals-X.Y.Z-py3-none-any.whl"
```

In the container image every module is installed and off; enable one with
`LAW_MCP_SOURCE_MODULES=-*,<name>`.

### Added

- New feature description

### Changed

- Changed feature description

### Deprecated

- Deprecated feature description

### Removed

- Removed feature description

### Fixed

- Fixed bug description

### Security

- Security fix description
