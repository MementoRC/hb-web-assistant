# hb-web-assistant

[![CI](https://github.com/MementoRC/hb-web-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/MementoRC/hb-web-assistant/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/MementoRC/hb-web-assistant)](https://codecov.io/gh/MementoRC/hb-web-assistant)
[![PyPI version](https://badge.fury.io/py/hb-web-assistant.svg)](https://badge.fury.io/py/hb-web-assistant)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

REST + WebSocket client assistants and token-bucket throttler for Hummingbot, extracted from
`hummingbot.core.web_assistant` and `hummingbot.core.api_throttler`.

## Overview

This package provides the HTTP/WebSocket client abstractions and API rate-limiting infrastructure
used by 249+ Hummingbot exchange connectors. It operates as a standalone library and is a
drop-in replacement for the inlined modules in Hummingbot core.

The import path `web_assistant` (no `hb_` prefix) is intentional: it preserves continuity
for existing consumers without requiring import renaming.

## Features

- **REST Assistants**: Authenticated and unauthenticated HTTP clients built on aiohttp
- **WebSocket Assistants**: Managed WebSocket connections with heartbeat and reconnect logic
- **API Throttler**: Token-bucket rate limiter with per-endpoint and global limits
- **Async-First**: Built on asyncio; all I/O is non-blocking
- **Strict Typing**: mypy strict mode throughout

## Installation

```bash
# Clone the repository
git clone https://github.com/MementoRC/hb-web-assistant.git
cd _scaffold-hb-web-assistant

# Pixi (recommended)
pixi install
pixi run test

# pip
pip install -e ".[dev]"
```

## Project Structure

```
_scaffold-hb-web-assistant/
├── web_assistant/               # Package source
│   ├── __init__.py              # Package exports
│   ├── __about__.py             # Version: "0.1.0"
│   └── throttler/               # API throttler (extracted from api_throttler)
│       └── __init__.py
├── tests/                       # Test suite
│   ├── __init__.py
│   └── test_smoke.py            # Import and version smoke test
├── .github/                     # CI/CD workflows
├── pyproject.toml               # Primary configuration
├── pixi.toml                    # Pixi environment
└── .pre-commit-config.yaml      # Pre-commit hooks
```

## Running Tests

```bash
# Pixi (recommended)
pixi run test               # All tests
pixi run lint               # Lint check
pixi run typecheck          # Type check
pixi run check              # Full quality + test suite

# pytest directly
pytest tests/
```

## Supersedes

This package supersedes:
- `hummingbot.core.web_assistant` — REST and WebSocket client assistants
- `hummingbot.core.api_throttler` — Token-bucket API rate limiter

## License

This project is licensed under the Apache 2.0 License - see the [LICENSE](LICENSE) file for details.
