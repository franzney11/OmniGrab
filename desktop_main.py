"""Punto de entrada de OmniGrab como aplicación de escritorio Windows."""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
import webview
from backend.main import app


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class DesktopAPI:
    def choose_folder(self, initial_dir: str | None = None) -> str | None:
        """Abre el selector nativo desde la ruta indicada o el padre válido más cercano."""
        if not webview.windows:
            return None
        candidate = Path(initial_dir).expanduser() if initial_dir else Path.home()
        if not candidate.is_absolute():
            candidate = Path.home() / candidate
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        if not candidate.is_dir():
            candidate = Path.home()
        result = webview.windows[0].create_file_dialog(
            webview.FOLDER_DIALOG, directory=str(candidate),
        )
        return str(result[0]) if result else None

    def close_window(self) -> None:
        if webview.windows:
            webview.windows[0].destroy()


def main() -> None:
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="OmniGrab-API", daemon=True)
    thread.start()

    # Espera a que la API local esté lista antes de abrir la ventana.
    import urllib.request
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        if not thread.is_alive():
            raise RuntimeError("No se pudo iniciar el servidor local de OmniGrab.")
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1):
                break
        except Exception:
            time.sleep(0.2)
    else:
        raise RuntimeError("OmniGrab tardó demasiado en iniciar.")

    window = webview.create_window(
        "OmniGrab", url, width=1120, height=820, min_size=(860, 620),
        background_color="#09090b",
        js_api=DesktopAPI(),
    )
    window.events.closed += lambda: setattr(server, "should_exit", True)
    webview.start(gui="edgechromium", debug=False)
    server.should_exit = True
    thread.join(timeout=3)


if __name__ == "__main__":
    main()
