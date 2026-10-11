# Jev Assist — Current Project State & Agent Handover

Updated: 2026-10-11
Branch: `main` (clean, 45/45 tests passing)

---

## 1. What This Project Is

A fast, privacy-preserving, hybrid browser automation agent running in **Brave** (or Chrome):
- **Gear 1 (System 1):** Local fine-tuned 322M decision model (`laya-browser-distilled` on port 8791). Picks DOM element and operation in **~75 ms** locally on the RTX 4070 Ti (zero cloud API cost for routine clicks/fills).
- **Gear 2 (System 2):** Vision VLM fallback (`claude-odysseus` via OmniRoute / Gemini). Wakes up automatically when Gear 1 confidence < 0.65, blocked, or in a loop. Inspects on-demand screenshot and emits either an element ID or raw `(x, y)` CDP mouse clicks (for canvas/unlabelled icons).
- **Planner / Chat Layer:** Conversational multi-turn manager (`chat_agent.py`) that maintains user profile facts across turns, extracts facts from attached passports/PDFs, and asks for missing details.
- **Brave Side Panel Extension:** Manifest V3 extension in `extension/` that docks on the right side of Brave and drives the active tab directly via `chrome.debugger` WebSocket relay (port 8767).

---

## 2. Port & Process Architecture

```text
Brave Browser (Active Tab)
   │ (chrome.debugger)
   ▼
[extension/background.js]
   │ (ws://127.0.0.1:8767)
   ▼
[jev_ultrafast/relay.py] ◄─── Driven synchronously by Agent/Browser
   ▲
   │
[jev_ultrafast/demo.py] (http://127.0.0.1:8766)
   ├── Serves Side Panel API (/api/chat/*, /api/assist/*)
   ├── Bridges chat messages to chat_agent.py & Agent
   └── Hosts test forms (/application.html, /fixture.html)
   ▲
   │ (http://127.0.0.1:8791/v1/systemone)
   ▼
[laya/laya-browser/laya_browser.py]
   └── Runs laya-browser-distilled (322M weights on CUDA)
```

| Port | Service | Started By | Description |
|---|---|---|---|
| **8791** | Local Distilled Laya | `laya/.venv` python script | Local System-1 decision head (~75ms per step) |
| **8766** | Jev Web Server & Bridge | `python -m uv run jev` | Extension REST API & test form server |
| **8767** | DevTools WebSocket Relay | `jev_ultrafast/relay.py` | Bridges DevTools commands between Agent and Brave tab |
| **20128** | OmniRoute AI Gateway | System background | Local gateway providing text & vision LLMs |

---

## 3. How to Start Everything

One-click startup:
```text
C:\Users\Zein\open-browser-automation\start.bat
```

Or manually in two terminals:
```powershell
# Terminal 1: Local decision model
cd C:\Users\Zein\open-browser-automation\laya
.venv\Scripts\python laya-browser\laya_browser.py serve --port 8791 --model laya-browser-distilled

# Terminal 2: Assistant server & extension relay
cd C:\Users\Zein\open-browser-automation\jev-ultrafast
python -m uv run --env-file .env jev
```

To load the extension in Brave:
1. Open `brave://extensions`.
2. Toggle Developer mode ON -> Click "Load unpacked".
3. Select `C:\Users\Zein\open-browser-automation\jev-ultrafast\extension`.

---

## 4. Key Files & Responsibilities

- **`jev_ultrafast/agent.py`:** Main loop. Manages Gear 1 + Gear 2 handoff, on-demand screenshots, approval gate on sensitive clicks (`submit`, `pay`, `sign`), coordinate clicking (`kind="coords"`), and file upload actions.
- **`jev_ultrafast/model.py`:** Decision interface. Contains `choose()` (calls Laya 8791), `choose_vision()` (multimodal VLM), and `field_text()` (extracts field text from profile or raises `MissingValue`).
- **`jev_ultrafast/snapshot.js`:** Single-call DOM observer. Deep tree-walker that traverses across same-origin `<iframe>`s and open `shadowRoot`s (essential for modern web components like German Federal forms). Converts `target="_blank"` to `_self`.
- **`jev_ultrafast/browser.py`:** Low-level browser driver. Dispatches mouse clicks, coordinates, typing, scrolling, and file attachments via CDP `DOM.setFileInputFiles`.
- **`jev_ultrafast/relay.py`:** Synchronous-to-async WebSocket bridge connecting Python agent to Brave's `chrome.debugger` session.
- **`jev_ultrafast/bridge.py`:** Thread-safe conversational session manager. Holds message history, active tab URL, uploaded file cache, and profile facts with German/English alias normalization.
- **`jev_ultrafast/chat_agent.py`:** Multi-turn planner. Formats context, reads documents, outputs user replies, and determines browser action goals.
- **`jev_ultrafast/planner.py`:** CLI entrypoint for `jev-assist` (interactive chat mode or single-command runs).
- **`scripts/stress_test.py`:** Automated 12-scenario end-to-end stress test suite. Run with `python -m uv run python scripts/stress_test.py`.
- **`extension/`:** Manifest V3 side panel extension (`manifest.json`, `sidepanel.html`, `sidepanel.css`, `sidepanel.js`, `background.js`).

---

## 5. Verified Capabilities & Test Status

1. **45 / 45 unit tests passing** (`python -m uv run pytest`).
2. **All 12 stress scenarios passing** (`scripts/stress_test.py`):
   - Direct navigation from user intent (auto-finds target site, no manual URL needed).
   - Search box typing and form submission.
   - Multi-constraint dropdown, checkbox, and text filtering.
   - Halts and prompts user for missing data (never invents personal info).
   - Ingests passport/ID attachments (extracts name, DOB, passport no with zero prompts).
   - Native file uploads via CDP `DOM.setFileInputFiles`.
   - Shadow DOM web components & iframe traversal.
   - Sensitive action safety gate blocks or permits submission on user approval.
   - Multi-turn conversation memory.
   - Clean handling of impossible tasks.
3. **Live German Government Form (`leipzig.de`):** Tested live on the official Ausländerbehörde student permit renewal portal, successfully penetrating the Federal Web Component `<at-ausbildung>` inside the Shadow DOM and navigating 13 live steps.

---

## 6. What the Next Agent Needs to Know

- **Git Authorship:** User global rule strictly forbids AI attribution tags (`Co-Authored-By`, `Generated with`, Claude tags). Keep git commits authored solely by `ZeroTrustZein`.
- **Speed Optimization (Open Opportunity):** Currently, the conversational planner uses `claude-odysseus` via OmniRoute, which includes reasoning tokens and takes 6–9s per chat turn. Switching the planner / text helper to a faster non-reasoning model (e.g. Gemini 2.5 Flash / Flash-Lite or Mercury) will cut turn latency down to ~1s.
- **Environment config:** `.env` in `jev-ultrafast/` holds `TEXT_MODEL_API_KEY`, `TEXT_MODEL_BASE_URL=http://127.0.0.1:20128`, `PYTHONUTF8=1`.
- **Extension copy:** `jev-ultrafast/extension/` is tracked in git; `open-browser-automation/extension/` is the unpacked directory loaded into Brave. Keep both in sync with `cp -r jev-ultrafast/extension/* extension/`.
