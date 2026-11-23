"""Conversational browser agent for Jev Assist.

Maintains multi-turn memory, plans web actions, drives the browser with Gear 1 (75ms local) + Gear 2 (Vision),
and holds an interactive dialogue with the user just like Tabbit AI.
"""

import json
import os
from pathlib import Path

from .contracts import (
    OutcomeEvaluation,
    SubGoal,
    evidence_holds,
    field_values,
    missing_goal_values,
    normalize_subgoals,
    requirement_check,
    subject_missing,
)
from .model import json_content, post_json

CHAT_SYSTEM = """You are Jev Assist, an AI browser co-pilot and conversational agent. You help users
automate web tasks: editing pages and wikis, researching, navigating portals, filling forms and applications,
booking appointments, and uploading documents.

You have two jobs:
1. TALK to the user: Be clear, proactive, and concise. Explain what you found, what you did, what assumptions you made,
   and ask clearly for missing personal details or documents needed by the task.
2. DRIVE the browser: Decide the next website/form step for the fast browser agent.

CURRENT ACTIVE TAB & URL HANDLING (CRITICAL):
- The prompt prominently displays "CURRENT ACTIVE TAB: <url>".
- This is the webpage currently open in the user's browser in front of them right now.
- If CURRENT ACTIVE TAB is provided and the user refers to "this tab", "this page", "here", "the page",
  or asks to edit, add, insert, or fill something without naming a different site:
  * You MUST immediately use CURRENT ACTIVE TAB.
  * Set "start_url" in the first sub_goal to null to stay on the page (or to that URL).
  * NEVER claim the URL is missing or ask the user for a page link/URL when CURRENT ACTIVE TAB is provided!
  * NEVER hallucinate unrelated websites, "timetable portal", or generic forms. Base your plan on the active tab.
  * If CURRENT ACTIVE TAB is a Wikipedia article or edit page, you are editing that exact Wikipedia page.

DIRECT DEEP URL ROUTING (CRITICAL):
- Never dump users onto generic root homepages when a direct URL is standard:
  * YouTube creators/channels: use direct URL `https://www.youtube.com/@<creator>` (e.g. `https://www.youtube.com/@Caedrel`).
  * Amazon search queries: use direct search URL `https://www.amazon.com/s?k=<query>` (e.g. `https://www.amazon.com/s?k=RTX+4080+Super`).
  * Azure App Registrations: use direct blade URL `https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade`.
  * Reddit subreddits: use `https://www.reddit.com/r/<subreddit>`.
  * Wikipedia articles: use `https://en.wikipedia.org/wiki/<Article_Title>`.

MULTI-STEP SUB-GOAL DECOMPOSITION (CRITICAL):
- Break compound or multi-step requests into 2–7 sequential, atomic "sub_goals":
  * E.g. "build a PC on Amazon for $2500":
    1. Search 'RTX 4080 Super', open top result, click 'Add to Cart'.
    2. Search 'Ryzen 7 7800X3D', open top result, click 'Add to Cart'.
    3. Search '32GB DDR5 6000MHz RAM', open top result, click 'Add to Cart'.
  * E.g. "register an app on Azure Portal":
    1. Open App Registrations blade directly and click 'New registration'.
    2. Enter app name 'Claude Outlook', select account type, enter redirect URI, and click 'Register'.
- Single-step requests use exactly 1 sub_goal.

PAGE EDITING & INSERTING ENTRIES (CRITICAL):
- When the user asks to add or insert an entry into an open page, list, document, or wiki:
  * "action_required" MUST be true.
  * Formulate a sub_goal that explicitly instructs the agent to:
    1. Locate and click into the text editor / wikitext area (switch to source editing if visual editor is active).
    2. Insert the entry formatted properly into the editor at the correct location (e.g. alphabetical order).
    3. Fill the edit summary if present (e.g. "Add <entry>").
    4. Click "Show preview" (or preview button) so the edit is staged and visible to the user before publishing.
    5. The sub_goal must explicitly instruct the agent to click into the editor and insert the entry.
       Never stop after just scrolling or opening the editor!
  * In "reply", inform the user what entry will be added, where it will be placed, and that it will be previewed.

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
  "reply": "Your markdown-formatted message to user. Outline the plan and list the steps.",
  "action_required": true/false,
  "sub_goals": [
    {
      "goal": "Concrete instructions for local policy (buttons to click, fields to fill, where to stop)",
      "start_url": "Direct target URL, or null if staying on current page",
      "completion_criteria": "Verifiable outcome (e.g. URL pattern or visible text on page confirming completion)"
    }
  ],
  "extracted_profile": {"fact_name": "value extracted from document or message"},
  "questions_for_user": ["List of specific missing details you need the user to provide"]
}

Guidelines:
- If the user says 'do everything for me' or gives a goal, outline the plan, find the target page/portal, and start.
- When asked to search, find, navigate, open, filter, fill, or edit,
  action_required MUST be true and sub_goals MUST be non-empty.
- Search fields: instruct the agent to type the query AND click Search / Submit to execute the search.
- SHOWTIMES: In German cinemas, English version is OV or OmU. If requested timeframe is unavailable, pick closest.
- SCOPE: if the user names specific fields ("fill my email"), the goal must touch ONLY those fields and then stop.
  Write it as "Fill ONLY <fields>, leave every other field untouched, then stop." Never fill extra fields.
- NEVER invent personal information. If not in the documents or message, ask the user directly in bullet points.
- Always warn before final submission or payment. Never submit without explicit user go-ahead.
"""


def chat_turn(conversation_history, profile, current_url=None, files=()):
    base = (
        os.environ.get("PLANNER_BASE_URL") or os.environ.get("TEXT_MODEL_BASE_URL", "http://127.0.0.1:20128")
    ).rstrip("/")
    key = os.environ.get("PLANNER_API_KEY") or os.environ.get("TEXT_MODEL_API_KEY", "local")
    model = os.environ.get("PLANNER_MODEL") or os.environ.get("TEXT_MODEL", "claude-odysseus")

    from .planner import documents

    url_display = current_url.strip() if current_url and current_url.strip() else "None (blank/new tab)"
    prompt_lines = [
        f"CURRENT ACTIVE TAB: {url_display}",
        "",
        "CONTEXT:",
        f"- Known Profile: {json.dumps(profile, ensure_ascii=False) if profile else '{}'}",
        f"- Attached Files: {[Path(f).name for f in files] if files else '[]'}",
        "",
        "CONVERSATION HISTORY:",
    ]
    for m in conversation_history[-8:]:
        role = "USER" if m.get("role") == "user" else "ASSISTANT"
        prompt_lines.append(f"{role}: {m.get('content', '')}")

    prompt_lines.extend([
        "",
        "INSTRUCTIONS FOR THIS TURN:",
        f"1. Check CURRENT ACTIVE TAB: {url_display}.",
        "2. If the user refers to 'this tab', 'this page', 'here', or asks to add/edit/fill without specifying",
        "   a new URL, use CURRENT ACTIVE TAB immediately.",
        "3. If asked to add or insert an entry to a page/list/wiki, formulate a goal that explicitly instructs",
        "   the agent to click into the editor and insert the entry properly formatted.",
        "4. Return only the JSON object as specified in the system prompt."
    ])

    user_text = "\n".join(prompt_lines)
    parts = [{"type": "text", "text": user_text}]
    for f in files:
        try:
            parts += documents([f])
        except Exception as exc:  # a corrupt or exotic file must never abort the whole task
            parts.append({"type": "text", "text": f"Document {Path(f).name}: could not be read ({exc})"})

    body = {
        "model": model,
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": CHAT_SYSTEM},
            {"role": "user", "content": parts},
        ],
    }
    if "odysseus" in model or "r1" in model or "o3" in model or "o1" in model:
        body["reasoning_effort"] = "low"

    result = post_json(base + "/chat/completions", key, body)
    return json_content(result)


SYNTHESIS_SYSTEM = """You are Jev Assist. The browser agent ran on a page pursuing the user's goal.
Evaluate visible evidence (page title, URL, visible text) against the USER GOAL.
Return JSON:
{
  "completed": true/false (true ONLY if the goal is visibly satisfied; false if stopped on a generic homepage,
    search listing, category/topic page, login or error page, or intermediate page; the requested item must be
    the page's main subject, not just a link, recent-searches chip or sidebar entry),
  "requirements": [{"need": "each requirement of the goal: every item, value, direction and unit, and the kind of
    page asked for (search results vs AI answers/topic/keyword page)", "met": true/false,
    "quote": "short exact quote from the page proving it, or empty"}] (only things that should be visible; never list
    prohibitions such as "do not sign in" as requirements),
  "summary": "2-4 clean markdown bullet points of what was reached or found, what remains to be done, and immediate next action. Under 100 words. No fluff."
}"""


def synthesize_outcome(goal, page):
    fallback_summary = f"Finished on page: [{page.get('title', 'Page')}]({page.get('url', '')})" if page else "Done"
    if not page or not page.get("text"):
        note = "The page shows no text, so success can't be checked."
        return {"completed": False, "summary": f"{fallback_summary}\n\n{note}"}
    base = (
        os.environ.get("PLANNER_BASE_URL") or os.environ.get("TEXT_MODEL_BASE_URL", "http://127.0.0.1:20128")
    ).rstrip("/")
    key = os.environ.get("PLANNER_API_KEY") or os.environ.get("TEXT_MODEL_API_KEY", "local")
    model = os.environ.get("PLANNER_MODEL") or os.environ.get("TEXT_MODEL", "claude-odysseus")

    prompt = (
        f"USER GOAL: {goal}\n"
        f"FINAL PAGE TITLE: {page.get('title', '')}\n"
        f"FINAL PAGE URL: {page.get('url', '')}\n"
        f"FORM FIELD VALUES:\n{field_values(page) or '(none)'}\n"
        f"PAGE TEXT CONTENT:\n{page.get('text', '')[:12000]}"
    )
    try:
        synth_body = {
            "model": model,
            "max_tokens": 800,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYNTHESIS_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        }
        if "odysseus" in model or "r1" in model or "o3" in model or "o1" in model:
            synth_body["reasoning_effort"] = "low"

        res = post_json(base + "/chat/completions", key, synth_body)
        data = json_content(res)
        if isinstance(data, dict):
            data = requirement_check({**data, "done": data.get("completed", False)})
            comp = bool(data.get("done"))
            summ = str(data.get("summary") or fallback_summary).strip()
            evidence = data.get("evidence")
            proof_sources = (page.get("text"), page.get("title"), page.get("url"), field_values(page))
            if comp and not evidence_holds(evidence, *proof_sources):
                # The model claimed success without quoting proof that is really on the page.
                comp = False
                summ += "\n\n(Not confirmed: no visible proof on the page.)"
            absent = missing_goal_values(goal, page) if comp else []
            if absent:
                comp = False
                summ += f"\n\n(Not confirmed: the page doesn't show {', '.join(absent)}.)"
            elif comp and subject_missing(goal, page):
                comp = False
                summ += "\n\n(Not confirmed: this page's title and address aren't about what you asked for.)"
            return {"completed": comp, "summary": summ, "evidence": evidence if comp else None}
        return {"completed": False, "summary": str(data).strip()}
    except Exception as exc:
        return {"completed": False, "summary": f"{fallback_summary}\n\nCould not check the result: {exc}"}
