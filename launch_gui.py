"""Open the local inspector from its macOS app bundle, without a terminal."""

from __future__ import annotations

import fcntl
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import webbrowser


APP_ID = "data-quality-inspector"
APP_VERSION = "0.15.0"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STARTUP_SECONDS = 8.0


def server_is_healthy(port: int, timeout: float = 0.4) -> bool:
    """Only reuse a loopback server identifying itself as this application."""
    if type(port) is not int or not 1 <= port <= 65535:
        return False
    connection = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        if response.status != 200:
            return False
        content = response.read(4097)
        if len(content) > 4096:
            return False
        data = json.loads(content)
        return isinstance(data, dict) and data.get("app") == APP_ID and data.get("version") == APP_VERSION
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def saved_port(state_file: Path) -> int | None:
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        port = state.get("port") if isinstance(state, dict) else None
        return port if type(port) is int and 1 <= port <= 65535 else None
    except (OSError, ValueError):
        return None


def save_launch_target(project: Path, port: int) -> None:
    """Publish a local browser target derived only from a validated port number."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("The inspector port must be an integer between 1 and 65535.")
    destination = project / "dqi" / "static" / "launch-target.js"
    temporary = destination.with_name("launch-target.js.tmp")
    temporary.write_text(f'window.DQI_APP_URL = "http://127.0.0.1:{port}/";\n', encoding="utf-8")
    temporary.replace(destination)


def available_port(preferred: int = DEFAULT_PORT) -> int:
    """Prefer the familiar URL; choose an ephemeral port if it is occupied."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind((HOST, preferred))
        except OSError:
            listener.bind((HOST, 0))
        return listener.getsockname()[1]


def _save_state(path: Path, port: int, pid: int | None):
    temporary = path.with_name("server.json.tmp")
    temporary.write_text(
        json.dumps({"app": APP_ID, "version": APP_VERSION, "port": port, "pid": pid}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _stop_owned_process(process):
    """Clean up only a process created by this launch attempt."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=0.3)


def _start_or_reuse(project: Path, runtime: Path, deadline: float) -> int:
    state_file = runtime / "server.json"
    candidates = list(dict.fromkeys(port for port in (saved_port(state_file), DEFAULT_PORT) if port is not None))
    for port in candidates:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("The local inspector did not become ready in time.")
        if server_is_healthy(port, timeout=min(0.4, remaining)):
            save_launch_target(project, port)
            return port

    for attempt in range(3):
        if time.monotonic() >= deadline:
            break
        port = available_port(DEFAULT_PORT if attempt == 0 else 0)
        process = None
        ready = False
        try:
            with (runtime / "server.log").open("ab", buffering=0) as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "dqi.web", "--port", str(port)],
                    cwd=project,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break  # A port-selection race may be resolved by another attempt.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if server_is_healthy(port, timeout=min(0.4, remaining)):
                    _save_state(state_file, port, process.pid)
                    save_launch_target(project, port)
                    ready = True
                    return port
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        finally:
            if process is not None and not ready:
                _stop_owned_process(process)
    raise RuntimeError("The local inspector could not start. Check runtime/server.log for details.")


def launch(project: Path | None = None) -> int:
    project = (project or Path(__file__).resolve().parent).resolve()
    runtime = project / "runtime"
    deadline = time.monotonic() + STARTUP_SECONDS
    try:
        runtime.mkdir(parents=True, exist_ok=True)
        # Concurrent double-clicks share one startup attempt rather than racing ports.
        with (runtime / "launcher.lock").open("a") as lock:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Another launch is still starting. Try opening the app again.")
                    time.sleep(0.1)
            port = _start_or_reuse(project, runtime, deadline)
        if not webbrowser.open(f"http://{HOST}:{port}/", new=2, autoraise=True):
            raise RuntimeError(f"The inspector is running, but the browser could not open. Visit http://{HOST}:{port}/.")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, webbrowser.Error) as error:
        message = f"Data Quality Inspector: {error}\n"
        try:
            with (runtime / "server.log").open("a", encoding="utf-8") as log:
                log.write(message)
        except OSError:
            pass
        print(message, file=sys.stderr, end="")
        return 1


if __name__ == "__main__":
    raise SystemExit(launch())
