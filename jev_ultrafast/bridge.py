"""State manager and background worker for the Brave/Chrome extension."""

import threading
import time


class AssistBridge:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = "idle"  # idle | running | waiting_user | done | error
        self.history = []
        self.pending_prompt = None
        self.pending_event = threading.Event()
        self.user_response = None
        self.final_result = None
        self.worker_thread = None
        self.error = None
        self.active_agent = None

    def start_task(self, request, docs=(), url=None, files=()):
        with self.lock:
            if self.status in ("running", "waiting_user"):
                raise RuntimeError("A task is already running")
            self.status = "running"
            self.history = []
            self.pending_prompt = None
            self.pending_event.clear()
            self.user_response = None
            self.final_result = None
            self.error = None

        def ask_user_callback(prompt):
            with self.lock:
                self.status = "waiting_user"
                self.pending_prompt = prompt
                self.pending_event.clear()
                self.user_response = None

            # Block worker thread until extension responds via /api/assist/respond
            self.pending_event.wait(timeout=300)
            with self.lock:
                ans = self.user_response or ("n" if "Allow?" in prompt else "")
                self.pending_prompt = None
                self.status = "running"
                return ans

        def report_callback(msg):
            with self.lock:
                self.history.append({"time": time.strftime("%H:%M:%S"), "msg": str(msg)})

        def worker():
            try:
                from .planner import ensure_local_server, run

                ensure_local_server()
                res = run(request, docs, url=url, files=files, ask_user=ask_user_callback, report=report_callback)
                with self.lock:
                    self.status = "done"
                    self.final_result = {
                        "done": res.get("done", False),
                        "missing": res.get("missing", ""),
                        "url": res.get("page", {}).get("url", ""),
                        "steps": len(res.get("history", [])),
                    }
                    report_callback(f"🎉 Complete! Final URL: {res.get('page', {}).get('url', '')}")
            except Exception as exc:
                with self.lock:
                    self.status = "error"
                    self.error = str(exc)
                report_callback(f"❌ Error: {exc}")

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def respond(self, text):
        with self.lock:
            if self.status != "waiting_user":
                raise RuntimeError("Agent is not currently waiting for user input")
            self.user_response = text
            self.pending_event.set()

    def stop(self):
        with self.lock:
            self.pending_event.set()
            self.status = "idle"
            self.error = "Stopped by user"

    def get_state(self):
        with self.lock:
            return {
                "status": self.status,
                "history": list(self.history),
                "pending_prompt": self.pending_prompt,
                "is_approval": bool(self.pending_prompt and "Allow?" in self.pending_prompt),
                "final_result": self.final_result,
                "error": self.error,
            }


BRIDGE = AssistBridge()
