"""Conversational session manager and extension bridge for Jev Assist."""

import json
import os
import threading
import time
from pathlib import Path

from .agent import Agent
from .chat_agent import chat_turn
from .model import MissingValue
from .questions import MAX_STEPS

# Side-panel attachments land here (artifacts/ is git-ignored), so the agent can read and upload them.
UPLOADS = Path.cwd() / "artifacts" / "uploads"
HISTORY_DIR = Path.cwd() / "artifacts" / "history"


def normalize_profile_aliases(prof):
    aliases = {
        "first_name": ["vorname", "first name", "given name"],
        "family_name": ["nachname", "surname", "last name", "family name"],
        "full_name": ["name", "fullname", "applicant name", "full name"],
        "date_of_birth": ["geburtsdatum", "birth date", "birthday", "date of birth"],
        "passport_number": ["passnummer", "passport", "id number", "passport number", "document number"],
        "postal_code": ["postleitzahl", "plz", "zip", "zip code", "postal code"],
        "nationality": ["staatsangehörigkeit", "citizenship", "nationality"],
    }
    for standard, syns in aliases.items():
        val = None
        for k in [standard] + syns:
            if k in prof and prof[k]:
                val = prof[k]
                break
        if val:
            for k in [standard] + syns:
                prof.setdefault(k, val)


class AssistBridge:
    def __init__(self):
        # Re-entrant: reset() holds the lock while calling stop(), which takes it again.
        self.lock = threading.RLock()
        self.status = "idle"  # "idle" | "thinking" | "running" | "waiting_user" | "error"
        self.messages = []
        self.profile = {}
        self.files = []
        self.current_url = None
        self.active_agent = None
        self.pending_event = threading.Event()
        self.user_input_response = None
        self.worker_thread = None
        self.msg_counter = 1
        self.session_id = f"sess_{int(time.time() * 1000)}"
        self.title = "New conversation"
        self._load_latest_or_init()

    def _save_session(self):
        try:
            if not self.messages:
                return
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            first_user = next((m.get("content") for m in self.messages if m.get("role") == "user"), None)
            if first_user and self.title == "New conversation":
                self.title = first_user[:45].replace("\n", " ").strip()
            data = {
                "id": self.session_id,
                "title": self.title,
                "updated_at": time.strftime("%Y-%m-%d %H:%M"),
                "messages": self.messages,
                "profile": self.profile,
                "files": self.files,
                "current_url": self.current_url,
                "status": self.status,
            }
            (HISTORY_DIR / f"{self.session_id}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _load_latest_or_init(self):
        try:
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            files = sorted(HISTORY_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
            if files:
                data = json.loads(files[0].read_text(encoding="utf-8"))
                self.session_id = data.get("id", files[0].stem)
                self.title = data.get("title", "Conversation")
                self.messages = data.get("messages", [])
                self.profile = data.get("profile", {})
                self.files = data.get("files", [])
                self.current_url = data.get("current_url")
                self.status = "idle"
                self.msg_counter = max([m.get("id", 0) for m in self.messages] + [0]) + 1
        except Exception:
            pass

    def list_history(self):
        try:
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            sessions = []
            for p in sorted(HISTORY_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    sessions.append({
                        "id": data.get("id", p.stem),
                        "title": data.get("title", "Untitled"),
                        "updated_at": data.get("updated_at", ""),
                        "message_count": len(data.get("messages", [])),
                        "active": (data.get("id") == self.session_id),
                    })
                except Exception:
                    continue
            return sessions
        except Exception:
            return []

    def load_session(self, sess_id):
        self.stop()
        p = HISTORY_DIR / f"{sess_id}.json"
        if not p.exists():
            raise ValueError(f"Session {sess_id} not found")
        data = json.loads(p.read_text(encoding="utf-8"))
        with self.lock:
            self.session_id = data.get("id", sess_id)
            self.title = data.get("title", "Conversation")
            self.messages = data.get("messages", [])
            self.profile = data.get("profile", {})
            self.files = data.get("files", [])
            self.current_url = data.get("current_url")
            self.status = "idle"
            self.msg_counter = max([m.get("id", 0) for m in self.messages] + [0]) + 1
        return self.get_state()

    def new_session(self):
        self._save_session()
        self.stop()
        with self.lock:
            self.session_id = f"sess_{int(time.time() * 1000)}"
            self.title = "New conversation"
            self.messages = []
            self.profile = {}
            self.files = []
            self.current_url = None
            self.status = "idle"
            self.msg_counter = 1
        return self.get_state()

    def add_file(self, name, data):
        """Store a file the user attached in the side panel; the agent may read it or upload it to forms."""
        UPLOADS.mkdir(parents=True, exist_ok=True)
        safe = Path(name).name or "attachment"
        path = UPLOADS / f"{int(time.time() * 1000)}-{safe}"
        path.write_bytes(data)
        with self.lock:
            self.files.append(str(path))
        return safe

    def send_message(self, text, files=(), url=None):
        with self.lock:
            if self.status == "waiting_user":
                # The agent is paused on a question: this message is the answer, not a new task.
                self.messages.append(
                    {"id": self.msg_counter, "role": "user", "content": text, "time": time.strftime("%H:%M")}
                )
                self.msg_counter += 1
                self.user_input_response = text
                self.pending_event.set()
                return
            if self.status in ("thinking", "running"):
                raise RuntimeError("Assistant is currently working on an action")
            self.status = "thinking"
            if url:
                self.current_url = url

            # Add user message
            user_msg = {
                "id": self.msg_counter,
                "role": "user",
                "content": text,
                "time": time.strftime("%H:%M"),
            }
            self.msg_counter += 1
            self.messages.append(user_msg)

        def worker():
            try:
                # 1. Ask conversational planner for response and next actions
                with self.lock:
                    history_for_llm = [{"role": m["role"], "content": m["content"]} for m in self.messages]
                    prof = dict(self.profile)
                    flist = list(self.files)
                    cur_url = self.current_url

                plan = chat_turn(history_for_llm, prof, current_url=cur_url, files=flist)

                # Update extracted profile facts and normalize synonyms (German/English)
                if plan.get("extracted_profile"):
                    with self.lock:
                        self.profile.update(plan["extracted_profile"])
                        normalize_profile_aliases(self.profile)

                # Create assistant response message
                with self.lock:
                    asst_msg = {
                        "id": self.msg_counter,
                        "role": "assistant",
                        "content": plan.get("reply", "Understood. Proceeding with your request."),
                        "steps": [],
                        "time": time.strftime("%H:%M"),
                    }
                    self.msg_counter += 1
                    self.messages.append(asst_msg)

                # 2. If the planner asked the user something, wait for the answer instead of driving blind.
                if plan.get("questions_for_user"):
                    with self.lock:
                        self.status = "idle"
                    return
                # Act when the planner formed a goal, navigated to a start_url, or requested action.
                goal = (plan.get("goal") or "").strip()
                action_required = plan.get("action_required") is True or bool(plan.get("start_url"))
                if action_required and not goal:
                    goal = text.strip()
                should_act = action_required and bool(goal)
                if should_act:
                    with self.lock:
                        self.status = "running"

                    from . import relay
                    from .planner import ensure_local_server

                    ensure_local_server()
                    # Drive the user's own tab through the extension. Never fall back to launching a separate
                    # browser: if the extension is not attached, fail loudly instead of opening Chrome.
                    relay.wait_connected(timeout=10)
                    os.environ["JEV_DRIVER"] = "extension"
                    target_url = plan.get("start_url") or cur_url or "https://html.duckduckgo.com/html/"

                    def step_callback(step_info):
                        with self.lock:
                            asst_msg["steps"].append(step_info)
                            self._save_session()

                    def approve_callback(action):
                        with self.lock:
                            self.status = "waiting_user"
                            content = (
                                f"⚠️ The agent wants to click **'{action['label']}'** (sensitive/final action). "
                                "Allow this click? [Yes / No]"
                            )
                            ask_msg = {
                                "id": self.msg_counter,
                                "role": "assistant",
                                "content": content,
                                "time": time.strftime("%H:%M"),
                                "is_approval": True,
                            }
                            self.msg_counter += 1
                            self.messages.append(ask_msg)
                            self._save_session()
                            self.pending_event.clear()
                            self.user_input_response = None

                        self.pending_event.wait(timeout=300)
                        with self.lock:
                            res = (self.user_input_response or "n").strip().lower()
                            self.status = "running"
                            return res in ("y", "yes", "true", "allow")

                    with Agent(
                        target_url, goal, profile=self.profile, approve=approve_callback, files=self.files
                    ) as agent:
                        self.active_agent = agent
                        while agent.state["status"] not in {"done", "blocked"}:
                            try:
                                for state in agent.run():
                                    # Each tick yields state; log an action only the first time it appears,
                                    # otherwise re-checks of the same page repeat the last action in the panel.
                                    if state.get("history") and state["history"][-1].get("step") not in {
                                        s.get("step") for s in asst_msg["steps"]
                                    }:
                                        last_h = state["history"][-1]
                                        gear = last_h.get("gear", 1)
                                        step_info = {
                                            "step": last_h.get("step"),
                                            "gear": gear,
                                            "action": last_h.get("action"),
                                            "kind": last_h.get("kind"),
                                            "text": last_h.get("text"),
                                            "time": time.strftime("%H:%M:%S"),
                                        }
                                        step_callback(step_info)
                            except MissingValue as missing:
                                # Form needs user information
                                prompt_text = f"The form needs your **{missing.field}**. What should I enter?"
                                with self.lock:
                                    self.status = "waiting_user"
                                    ask_msg = {
                                        "id": self.msg_counter,
                                        "role": "assistant",
                                        "content": f"⏸️ {prompt_text}",
                                        "time": time.strftime("%H:%M"),
                                        "missing_field": missing.field,
                                    }
                                    self.msg_counter += 1
                                    self.messages.append(ask_msg)
                                    self._save_session()
                                    self.pending_event.clear()
                                    self.user_input_response = None

                                # Wait for user to chat back with the missing answer
                                self.pending_event.wait(timeout=300)
                                with self.lock:
                                    ans = self.user_input_response or ""
                                    field_key = missing.field.lower().replace("-", "_").replace(" ", "_")
                                    self.profile[missing.field] = ans
                                    self.profile[field_key] = ans
                                    agent.profile[missing.field] = ans
                                    agent.profile[field_key] = ans
                                    normalize_profile_aliases(self.profile)
                                    normalize_profile_aliases(agent.profile)
                                    agent.state["goal"] += f"\nUser provided {missing.field}: {ans}"
                                    self.status = "running"

                        final_page = agent.state["page"]
                        with self.lock:
                            self.current_url = final_page.get("url")
                            if agent.state["status"] == "done":
                                asst_msg["content"] += (
                                    f"\n\n✅ **Completed!** Finished on page: [{final_page.get('title')}]"
                                    f"({final_page.get('url')})"
                                )
                            elif agent.state["status"] == "blocked":
                                reason = agent.state.get("denied")
                                if reason:
                                    msg = f"\n\n⛔ **Stopped:** Sensitive action '{reason}' was not approved."
                                elif len(agent.state.get("history", [])) >= MAX_STEPS:
                                    msg = f"\n\n⛔ **Stopped:** Reached maximum action limit ({MAX_STEPS} steps)."
                                else:
                                    last_dec = (agent.state.get("decisions") or [{}])[-1]
                                    specific_reason = last_dec.get("reason")
                                    if specific_reason:
                                        msg = f"\n\n⚠️ **Paused/Blocked:** {specific_reason}"
                                    else:
                                        msg = (
                                            f"\n\n⚠️ **Paused/Blocked:** Could not proceed further on "
                                            f"[{final_page.get('title')}]({final_page.get('url')}). "
                                            "Check if a CAPTCHA, login, or manual action is required."
                                        )
                                asst_msg["content"] += msg

                with self.lock:
                    self.status = "idle"
                    self._save_session()
            except Exception as exc:
                with self.lock:
                    self.status = "error"
                    err_msg = {
                        "id": self.msg_counter,
                        "role": "assistant",
                        "content": f"❌ **Error:** {exc}",
                        "time": time.strftime("%H:%M"),
                    }
                    self.msg_counter += 1
                    self.messages.append(err_msg)
                    self._save_session()

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def respond_input(self, text):
        with self.lock:
            self.user_input_response = text
            self.pending_event.set()

    def stop(self):
        worker = None
        with self.lock:
            self.pending_event.set()
            self.status = "idle"
            if self.active_agent:
                self.active_agent.state["status"] = "blocked"
            worker = self.worker_thread
            self._save_session()
        if worker and worker.is_alive() and worker != threading.current_thread():
            worker.join(timeout=2.0)

    def reset(self):
        return self.new_session()

    def get_state(self):
        with self.lock:
            return {
                "id": self.session_id,
                "title": self.title,
                "status": self.status,
                "messages": list(self.messages),
                "profile": dict(self.profile),
                "current_url": self.current_url,
                "files": [Path(f).name for f in self.files],
            }


BRIDGE = AssistBridge()
