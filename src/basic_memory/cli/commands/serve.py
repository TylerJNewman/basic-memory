"""Serve command - run the FastAPI REST API server.

Exposes the full Basic Memory REST API for remote access via CLI (--cloud),
HTTP clients, and sibling services. This is the recommended way to deploy
Basic Memory as a remote service (e.g., on Railway) without MCP overhead.
"""

from typing import Optional

import typer

from basic_memory.cli.app import app


@app.command()
def serve(
    host: str = typer.Option(
        "0.0.0.0", help="Host to bind to (use 0.0.0.0 for external connections)"
    ),
    port: int = typer.Option(8000, help="Port to listen on"),
    project: Optional[str] = typer.Option(None, help="Restrict server to single project"),
):  # pragma: no cover
    """Run the Basic Memory REST API server.

    Starts a uvicorn server hosting the FastAPI application with all
    /v2/projects/... endpoints. Use this instead of `basic-memory mcp`
    when you want direct REST API access without MCP protocol overhead.

    The CLI's --cloud flag routes through this server automatically.

    Examples:

        basic-memory serve
        basic-memory serve --host 0.0.0.0 --port 8000
        basic-memory serve --project quantum
    """
    import os

    import uvicorn

    # Constrain to single project if specified
    if project:
        from basic_memory.config import ConfigManager

        config_manager = ConfigManager()
        project_name, _ = config_manager.get_project(project)
        if not project_name:
            typer.echo(f"No project found named: {project}", err=True)
            raise typer.Exit(1)
        os.environ["BASIC_MEMORY_MCP_PROJECT"] = project_name

    uvicorn.run(
        "basic_memory.api.app:app",
        host=host,
        port=port,
        log_level="info",
    )
