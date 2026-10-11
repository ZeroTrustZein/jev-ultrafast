"""Loopback-only inspector for the Jev browser agent."""

import atexit
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .agent import Agent
from .bridge import BRIDGE
from .questions import MAX_STEPS

ROOT = Path(__file__).parent
PORT = int(os.environ.get("TYPESAFE_DEMO_PORT", "8766"))
ORIGIN = f"http://127.0.0.1:{PORT}"
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
AGENT = None


def load_environment():
    path = Path.cwd() / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def response_state():
    state = AGENT.snapshot() if AGENT else {"page": None, "status": "idle", "history": [], "decision": None}
    return {**state, "text_model": os.environ.get("TEXT_MODEL", "deepseek-chat"), "max_steps": MAX_STEPS}


def close_browser():
    global AGENT
    if AGENT:
        AGENT.close()
        AGENT = None


def command(name, body):
    global AGENT
    if name == "reset":
        scenario = body.get("scenario", "flights")
        if scenario not in {"travel", "research", "flights"}:
            raise ValueError("Unknown demo scenario")
        goal = body.get("goal", "").strip()
        if not goal or len(goal) > 2000:
            raise ValueError("Enter 1–2,000 characters")
        close_browser()
        AGENT = Agent(
            "https://www.google.com/travel/flights?hl=en"
            if scenario == "flights"
            else f"{ORIGIN}/fixture.html?scenario={scenario}",
            goal,
            screenshots=True,
            record_dir=Path.cwd() / "artifacts" / "frames" if body.get("record") else None,
        )
        AGENT.state["scenario"] = scenario
    else:
        if AGENT is None:
            raise ValueError("Start a demo first")
        AGENT.command(name, body)
    return response_state()


class Handler(BaseHTTPRequestHandler):
    def send(self, status, content, mime="application/json"):
        content = content if isinstance(content, bytes) else content.encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Demo-Token, X-File-Name")
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Demo-Token, X-File-Name")
        self.end_headers()

    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in {"127.0.0.1", "localhost"}:
            return self.send(403, "Forbidden", "text/plain")
        path = urlparse(self.path).path
        if path == "/api/state":
            with LOCK:
                return self.send(200, json.dumps(response_state()))
        if path in ("/api/assist/state", "/api/chat/state"):
            return self.send(200, json.dumps(BRIDGE.get_state()))
        if path == "/demo.mp4":
            video = ROOT.parent / "docs" / "demo.mp4"
            if video.exists():
                return self.send(200, video.read_bytes(), "video/mp4")
        files = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
            "/fixture.html": ("fixture.html", "text/html"),
            "/application.html": ("application.html", "text/html"),
        }
        if path not in files:
            return self.send(404, "Not found", "text/plain")
        name, mime = files[path]
        content = (ROOT / "static" / name).read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
        self.send(200, content, mime + "; charset=utf-8")

    def do_POST(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in {"127.0.0.1", "localhost"}:
            return self.send(403, json.dumps({"error": "Local demo requests only"}))
        path = urlparse(self.path).path
        if path == "/api/chat/upload":
            # Raw file bytes from the side panel's file picker; only the extension may store files on disk.
            if not (self.headers.get("Origin") or "").startswith("chrome-extension://"):
                return self.send(403, json.dumps({"error": "Uploads are accepted from the extension only"}))
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 25 * 1024 * 1024:
                    raise ValueError("Attachments must be between 1 byte and 25 MB")
                from urllib.parse import unquote

                name = BRIDGE.add_file(unquote(self.headers.get("X-File-Name", "attachment")), self.rfile.read(length))
                return self.send(200, json.dumps({"status": "stored", "name": name}))
            except Exception as exc:
                return self.send(400, json.dumps({"error": str(exc)}))
        if path.startswith(("/api/assist/", "/api/chat/")):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length)) if length > 0 else {}
                if path in ("/api/assist/start", "/api/chat/send"):
                    msg = body.get("message") or body.get("request", "")
                    BRIDGE.send_message(
                        msg,
                        files=body.get("files", []),
                        url=body.get("url"),
                    )
                    return self.send(200, json.dumps({"status": "started"}))
                elif path in ("/api/assist/respond", "/api/chat/respond"):
                    BRIDGE.respond_input(body.get("response", ""))
                    return self.send(200, json.dumps({"status": "responded"}))
                elif path in ("/api/assist/stop", "/api/chat/stop"):
                    BRIDGE.stop()
                    return self.send(200, json.dumps({"status": "stopped"}))
                elif path == "/api/chat/reset":
                    BRIDGE.reset()
                    return self.send(200, json.dumps({"status": "reset"}))
            except Exception as exc:
                return self.send(400, json.dumps({"error": str(exc)}))
        if (
            self.headers.get("X-Demo-Token") != TOKEN
            or self.headers.get("Origin") not in (None, ORIGIN)
        ):
            return self.send(403, json.dumps({"error": "Local demo requests only"}))
        if not LOCK.acquire(blocking=False):
            return self.send(409, json.dumps({"error": "A browser step is already running"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 8192:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            result = command(self.path.removeprefix("/api/"), body)
            self.send(200, json.dumps(result))
        except (ValueError, RuntimeError, TimeoutError) as error:
            self.send(400, json.dumps({"error": str(error)}))
        except Exception:
            self.send(500, json.dumps({"error": "Local demo failed; no automatic retry. Reset to recover."}))
        finally:
            LOCK.release()

    def log_message(self, *_args):
        pass


def main():
    load_environment()
    atexit.register(close_browser)
    from . import relay

    relay.start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Jev Ultrafast: {ORIGIN}  (extension relay ws://127.0.0.1:{relay.PORT})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
