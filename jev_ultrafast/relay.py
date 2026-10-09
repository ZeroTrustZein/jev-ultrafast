"""Drive the user's real Brave/Chrome tab through the Jev Assist extension instead of a separate debug browser.

The extension holds a chrome.debugger session on the tab the user started from and opens a WebSocket to this relay.
`cdp(method, **params)` sends one DevTools command over that socket and blocks for its reply, so the rest of the agent
keeps its synchronous browser_harness-style calls. Commands never leave 127.0.0.1.
"""

import asyncio
import itertools
import json
import os
import threading
from concurrent.futures import Future

from websockets.asyncio.server import serve

PORT = int(os.environ.get("JEV_RELAY_PORT", "8767"))
TIMEOUT = 30

_loop = None
_socket = None
_pending = {}
_ids = itertools.count(1)
_connected = threading.Event()
_started = threading.Lock()


class RelayError(RuntimeError):
    """The extension is not connected, or the browser rejected a command."""


async def _handler(socket):
    global _socket
    _socket = socket
    _connected.set()
    try:
        async for raw in socket:
            message = json.loads(raw)
            future = _pending.pop(message.get("id"), None)
            if future and not future.done():
                if "error" in message:
                    future.set_exception(RelayError(message["error"]))
                else:
                    future.set_result(message.get("result") or {})
    finally:
        if _socket is socket:
            _socket = None
            _connected.clear()
            for future in list(_pending.values()):
                if not future.done():
                    future.set_exception(RelayError("Extension disconnected"))
            _pending.clear()


def _origin_ok(connection, request):
    # Only the extension (chrome-extension://) or local tools may attach; no web page can drive the user's browser.
    origin = request.headers.get("Origin", "")
    if origin and not origin.startswith("chrome-extension://"):
        return connection.respond(403, "Forbidden\n")
    return None


def start():
    """Run the relay server on a background thread; safe to call more than once."""
    global _loop
    with _started:
        if _loop:
            return
        ready = threading.Event()

        def main():
            global _loop

            async def run():
                async with serve(_handler, "127.0.0.1", PORT, process_request=_origin_ok, max_size=2**26):
                    ready.set()
                    await asyncio.Future()

            _loop = asyncio.new_event_loop()
            _loop.run_until_complete(run())

        threading.Thread(target=main, daemon=True, name="jev-relay").start()
        ready.wait(5)


def connected():
    return _connected.is_set()


def wait_connected(timeout=10):
    start()
    if not _connected.wait(timeout):
        raise RelayError("Jev Assist extension is not connected. Open the extension popup in Brave and start a task.")


def cdp(method, session_id=None, **params):
    """Same call shape as browser_harness.helpers.cdp; session_id is accepted and ignored (one tab, one session)."""
    wait_connected()
    message_id = next(_ids)
    future = Future()
    _pending[message_id] = future
    payload = json.dumps({"id": message_id, "method": method, "params": params})
    asyncio.run_coroutine_threadsafe(_socket.send(payload), _loop)
    try:
        return future.result(TIMEOUT)
    except TimeoutError:
        _pending.pop(message_id, None)
        raise RelayError(f"{method} timed out after {TIMEOUT}s") from None
