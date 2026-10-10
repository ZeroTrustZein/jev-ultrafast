"""Conversational browser agent for Jev Assist.

Maintains multi-turn memory, plans web actions, drives the browser with Gear 1 (75ms local) + Gear 2 (Vision),
and holds an interactive dialogue with the user just like Tabbit AI.
"""

import json
import os
from pathlib import Path

from .model import json_content, post_json

CHAT_SYSTEM = """You are Jev Assist, a conversational browser agent. You help users navigate official portals,
fill complex applications, book appointments, and upload documents.

You have two jobs:
1. TALK to the user: Be clear, proactive, and concise. Explain what you found, what you did, what assumptions you made,
   and ask clearly for missing personal details or documents needed by the form.
2. DRIVE the browser: Decide the next website/form step for the fast browser agent.

Return a JSON object:
{
  "reply": "Your markdown-formatted message to user. Use emojis (✅, ⏸️, ⛔, ℹ️) and clean checklists.",
  "action_required": true/false,
  "start_url": "URL to navigate to, or null if staying on current page",
  "goal": "One paragraph concrete instructions for browser agent (buttons to click, fields to fill, where to stop)",
  "extracted_profile": {"fact_name": "value extracted from user message"},
  "questions_for_user": ["List of specific missing details you need the user to provide"]
}

Guidelines:
- If the user says 'do everything for me' or gives a goal, outline the plan, find the portal, and start filling.
- NEVER invent personal information (names, passport numbers, birth dates, addresses, postal codes, insurance).
  If the form asks for them and you do not have them in the profile, ask the user directly in bullet points.
- Always warn before final submission or payment. Never submit without explicit user go-ahead.
"""


def chat_turn(conversation_history, profile, current_url=None, files=()):
    base = (os.environ.get("PLANNER_BASE_URL") or os.environ.get("TEXT_MODEL_BASE_URL", "http://127.0.0.1:20128")).rstrip("/")
    key = os.environ.get("PLANNER_API_KEY") or os.environ.get("TEXT_MODEL_API_KEY", "local")
    model = os.environ.get("PLANNER_MODEL") or os.environ.get("TEXT_MODEL", "claude-odysseus")

    from .planner import documents

    context = {
        "current_url": current_url,
        "known_profile": profile,
        "available_files": [Path(f).name for f in files],
        "conversation": conversation_history[-8:],
    }
    # The planner reads the attachments themselves (PDF text, images, text files), not just their names.
    parts = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]
    for f in files:
        try:
            parts += documents([f])
        except (ValueError, UnicodeDecodeError, OSError) as exc:
            parts.append({"type": "text", "text": f"Document {Path(f).name}: could not be read ({exc})"})

    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": CHAT_SYSTEM},
                {"role": "user", "content": parts},
            ],
        },
    )
    return json_content(result)
