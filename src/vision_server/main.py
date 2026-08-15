"""Application entry points.

``python -m vision_server`` serves the stdio MCP transport; ``uvicorn
vision_server.main:app`` serves the HTTP/OpenAPI and Streamable HTTP MCP
transports.
"""

from __future__ import annotations

import asyncio

from .config import Settings
from .runtime import Runtime
from .transports import create_app, run_stdio

__all__ = ["app", "create_app", "main_stdio"]

app = create_app()


def main_stdio() -> None:
    """Console entry point for the stdio MCP transport."""
    asyncio.run(run_stdio(Runtime(Settings())))
