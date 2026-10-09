"""Conversational session manager and extension bridge for Jev Assist."""

import threading
import time
from pathlib import Path

from .agent import Agent
from .chat_agent import chat_turn
from .model import MissingValue


class AssistBridge:
    def __init__(self):
        self.lock = threading.Lock()
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

    def send_message(self, text, files=(), url=None):
        with self.lock:
            if self.status in ("thinking", "running"):
                raise RuntimeError("Assistant is currently working on an action")
            self.status = "thinking"
            if url:
                self.current_url = url
            if files:
                self.files.extend([str(Path(f).resolve()) for f in files if Path(f).exists()])

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

                # Update extracted profile facts
                if plan.get("extracted_profile"):
                    with self.lock:
                        self.profile.update(plan["extracted_profile"])

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

                # 2. If action is required, drive the browser
                if plan.get("action_required") and plan.get("goal"):
                    with self.lock:
                        self.status = "running"

                    from .planner import ensure_local_server

                    ensure_local_server()
                    # Use extension driver if available, else local browser
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
