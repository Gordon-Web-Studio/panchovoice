"""Core API server (`pv serve`)."""

from __future__ import annotations

import threading
import webbrowser

from .app import create_app

__all__ = ["create_app", "serve"]


def serve(port: int = 8700, open_browser: bool = False, config_path: str | None = None, host: str = "127.0.0.1") -> int:
    import uvicorn
    from rich.console import Console

    from .. import db
    from ..config import load_config
    from ..errors import ConfigError
    from ..mocktools import port_is_free

    console = Console()
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        console.print(f"[red]Config error[/] in {exc.source}:")
        for e in exc.errors:
            console.print(f"  {e.path or '(file)'}: {e.message}")
        return 2
    if not port_is_free(port, host):
        console.print(f"[red]Port {port} is in use.[/] Stop the other server or use `pv serve --port <other>`.")
        return 1
    app = create_app(config, mock_base_url=f"http://127.0.0.1:{port}/mock")
    base = f"http://127.0.0.1:{port}"
    console.print(f"PanchoVoiceAI server on [bold]{base}[/] (data in {config.data_path})")
    if app.state.web_dir is None:
        from ..probe import repo_root

        web = config.root / "web" if (config.root / "web" / "package.json").is_file() else repo_root() / "web"
        console.print(f"[yellow]The panel is not built.[/] Build it once with: cd {web} && npm install && npm run build")
    if open_browser:
        page = "/onboarding/" if not db.list_runs(config.db_path, limit=1) else "/"
        threading.Timer(1.5, lambda: webbrowser.open(base + page)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
