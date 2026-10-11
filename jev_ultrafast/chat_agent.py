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

DOCUMENT FACT EXTRACTION (CRITICAL):
- When the user attaches their passport, ID card, visa, enrollment cert, or contract, you MUST examine it immediately
  and extract all available facts into "extracted_profile":
  * full_name, first_name (Vorname), family_name / surname (Nachname)
  * date_of_birth (Geburtsdatum), place_of_birth, country_of_birth
  * passport_number / document_number, expiry_date, issue_date
  * nationality / citizenship
  * address / street / postal_code (if on the document)
- If an applicant detail is ALREADY visible in their attached passport or document, DO NOT ask the user for it!
- Only include fields in "questions_for_user" that are TRULY missing from both their documents and message.

Return a JSON object:
{
  "reply": "Your markdown-formatted message to user. Use emojis (✅, ⏸️, ⛔, ℹ️) and clean checklists.",
  "action_required": true/false,
  "start_url": "URL to navigate to, or null if staying on current page",
  "goal": "One paragraph concrete instructions for browser agent (buttons to click, fields to fill, where to stop)",
  "extracted_profile": {"fact_name": "value extracted from document or message"},
  "questions_for_user": ["List of specific missing details you need the user to provide"]
}

Guidelines:
- If the user says 'do everything for me' or gives a goal, outline the plan, find the portal, and start filling.
- Whenever the user asks to search, find, navigate, open, filter, or fill, action_required MUST be true and goal MUST be non-empty.
- Search fields: when searching a term on a portal or site, instruct the agent to type the query AND click Search / Submit / Find stays to execute the search.
- SHOWTIMES & RESERVATIONS: In German cinemas, English version is OV (Originalversion) or OmU. If the user asks for a specific timeframe (like 'weekend') but only another day (e.g. Monday) is scheduled, book the closest available showing rather than halting.
- SCOPE: if the user names specific fields ("fill my email"), the goal must touch ONLY those fields and then stop.
  Write it as "Fill ONLY <fields>, leave every other field untouched, then stop." Never fill extra fields.
- NEVER invent personal information. If not in the documents or message, ask the user directly in bullet points.
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
        except Exception as exc:  # a corrupt or exotic file must never abort the whole task
            parts.append({"type": "text", "text": f"Document {Path(f).name}: could not be read ({exc})"})

    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 512,
            "reasoning_effort": "low",
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": CHAT_SYSTEM},
                {"role": "user", "content": parts},
            ],
        },
    )
    return json_content(result)


SYNTHESIS_SYSTEM = """You are Jev Assist. The browser agent just reached the final page for the user's goal.
Read the page title, URL, and visible page text to provide a concise, high-value summary of what was found or completed.
Key instructions:
- If showtimes, flights, or tickets were found: specify the theater/airline, exact date/time, and format (e.g. note that OV means English Original Version).
- If an application/form was processed: summarize the fields filled and the current status.
- State the immediate next action available (e.g. 'Click ExpressTicket to select seats', or 'Form ready for review').
Format as 2-4 clean markdown bullet points. Keep it under 100 words. No fluff."""


def synthesize_outcome(goal, page):
    if not page or not page.get("text"):
        return f"Finished on page: [{page.get('title', 'Page')}]({page.get('url', '')})"
    base = (os.environ.get("PLANNER_BASE_URL") or os.environ.get("TEXT_MODEL_BASE_URL", "http://127.0.0.1:20128")).rstrip("/")
    key = os.environ.get("PLANNER_API_KEY") or os.environ.get("TEXT_MODEL_API_KEY", "local")
    model = os.environ.get("PLANNER_MODEL") or os.environ.get("TEXT_MODEL", "claude-odysseus")

    prompt = (
        f"USER GOAL: {goal}\n"
        f"FINAL PAGE TITLE: {page.get('title', '')}\n"
        f"FINAL PAGE URL: {page.get('url', '')}\n"
        f"PAGE TEXT CONTENT:\n{page.get('text', '')[:4000]}"
    )
    try:
        res = post_json(
            base + "/chat/completions",
            key,
            {
                "model": model,
                "max_tokens": 300,
                "reasoning_effort": "low",
                "messages": [
                    {"role": "system", "content": SYNTHESIS_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        msg = (res.get("choices") or [{}])[0].get("message", {})
        content = msg.get("content") or ""
        return content.strip()
    except Exception:
        return f"Finished on page: [{page.get('title', 'Page')}]({page.get('url', '')})"

