"""Conversational session manager and extension bridge for Jev Assist."""

import os
import threading
import time
from pathlib import Path

from .agent import Agent
from .chat_agent import chat_turn
from .model import MissingValue

# Side-panel attachments land here (artifacts/ is git-ignored), so the agent can read and upload them.
UPLOADS = Path.cwd() / "artifacts" / "uploads"


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
                # A concrete goal means act; the model sometimes writes one yet sets action_required false.
                if plan.get("goal") and (plan.get("action_required") or plan.get("start_url")):
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
                            self.pending_event.clear()
                            self.user_input_response = None

                        self.pending_event.wait(timeout=300)
                        with self.lock:
                            res = (self.user_input_response or "n").strip().lower()
                            self.status = "running"
                            return res in ("y", "yes", "true", "allow")

                    with Agent(
                        target_url, plan["goal"], profile=self.profile, approve=approve_callback, files=self.files
                    ) as agent:
                        self.active_agent = agent
                        while agent.state["status"] not in {"done", "blocked"}:
                            try:
                                for state in agent.run():
                                    if state.get("history"):
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
                                    self.pending_event.clear()
                                    self.user_input_response = None

                                # Wait for user to chat back with the missing answer
                                self.pending_event.wait(timeout=300)
                                with self.lock:
                                    ans = self.user_input_response or ""
                                    self.profile[missing.field] = ans
                                    agent.profile[missing.field] = ans
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

                with self.lock:
                    self.status = "idle"
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

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def respond_input(self, text):
        with self.lock:
            self.user_input_response = text
            self.pending_event.set()

    def stop(self):
        with self.lock:
            self.pending_event.set()
            self.status = "idle"
            if self.active_agent:
                self.active_agent.state["status"] = "blocked"

    def reset(self):
        with self.lock:
            self.stop()
            self.messages = []
            self.profile = {}
            self.files = []
            self.current_url = None
            self.status = "idle"

    def get_state(self):
        with self.lock:
            return {
                "status": self.status,
                "messages": list(self.messages),
                "profile": dict(self.profile),
                "current_url": self.current_url,
                "files": [Path(f).name for f in self.files],
            }


BRIDGE = AssistBridge()
