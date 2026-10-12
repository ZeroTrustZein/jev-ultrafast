"""Benchmark 5 challenging end-to-end tasks testing Jev Assist speed and accuracy."""

import argparse
import asyncio
import json
import threading
import time
import urllib.request
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp
from websockets.asyncio.client import connect

API = "http://127.0.0.1:8766"
APP = f"{API}/application.html"
FIXTURE = f"{API}/fixture.html?scenario=travel"


class Tab:
    def __init__(self, url):
        self.target = cdp("Target.createTarget", url=url)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self.ws = None
        self.loop = None
        threading.Thread(target=self._run_relay, daemon=True).start()
        time.sleep(1)

    def _run_relay(self):
        async def main():
            self.loop = asyncio.get_running_loop()
            async with connect("ws://127.0.0.1:8767", origin="chrome-extension://benchmark") as ws:
                self.ws = ws
                async for raw in ws:
                    m = json.loads(raw)
                    try:
                        res = await asyncio.to_thread(cdp, m["method"], session_id=self.session, **m["params"])
                        await ws.send(json.dumps({"id": m["id"], "result": res}))
                    except Exception as exc:
                        await ws.send(json.dumps({"id": m["id"], "error": str(exc)}))
        asyncio.run(main())

    def js(self, expression):
        r = cdp("Runtime.evaluate", session_id=self.session, expression=expression, returnByValue=True)
        return r.get("result", {}).get("value")

    def close(self):
        if self.ws and self.loop:
            try:
                asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop)
            except Exception:
                pass
        time.sleep(0.5)
        try:
            cdp("Target.closeTarget", targetId=self.target)
        except Exception:
            pass


def post(path, body=None, headers=None, data=None):
    req = urllib.request.Request(
        API + path,
        data=data if data is not None else json.dumps(body or {}).encode(),
        headers=headers or {"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def state():
    return json.loads(urllib.request.urlopen(API + "/api/chat/state", timeout=20).read())


def run_chat(message, url=None, answers=None, timeout=120):
    answers = answers or {}
    for retry in range(6):
        try:
            post("/api/chat/send", {"message": message, "url": url})
            break
        except Exception:
            time.sleep(1)
    seen, t0, answered = [], time.time(), set()
    while time.time() - t0 < timeout:
        time.sleep(1.5)
        s = state()
        last = s["messages"][-1] if s["messages"] else {}
        if last.get("role") == "assistant" and last["id"] not in answered:
            text = last.get("content", "")
            reply = None
            if last.get("is_approval"):
                reply = answers.get("approve", "no")
            elif s["status"] == "waiting_user" or last.get("missing_field") or (
                s["status"] == "idle" and ("?" in text or "what" in text.lower() or "need" in text.lower()) and "completed" not in text.lower()
            ):
                reply = next((v for k, v in answers.items() if k != "approve" and k.lower() in text.lower()), None)
            if reply is not None:
                seen.append(text[:160])
                answered.add(last["id"])
                try:
                    post("/api/chat/send", {"message": reply})
                except Exception:
                    pass
                continue
        if s["status"] in ("idle", "error") and time.time() - t0 > 5:
            return s, seen, round(time.time() - t0, 1)
    post("/api/chat/stop")
    return state(), seen, round(time.time() - t0, 1)


# ---- 5 Hard Tasks ----

def task_1_multi_filter():
    """Task 1: Search Lisbon + Design category + Free cancellation -> Casa Flora."""
    def check(t, s, p):
        hash_val = t.js("location.hash") or ""
        body_text = t.js("document.body.innerText") or ""
        ok = hash_val == "#casa-flora" and "Destination Lisbon" in body_text
        return ok, f"hash={hash_val!r} has_lisbon={'Destination Lisbon' in body_text}"

    return (
        "1. Multi-Constraint Filters & Dynamic Autocomplete",
        FIXTURE,
        None,
        "On this travel site, search Lisbon, set the Design category and Free cancellation, then open Casa Flora.",
        {},
        check,
    )


def task_2_shadow_and_iframe():
    """Task 2: Simultaneously penetrate open shadowRoot and nested iframe without touching other fields."""
    def check(t, s, p):
        street = t.js("document.getElementById('address-container').shadowRoot.getElementById('street').value") or ""
        agree = t.js("document.getElementById('decl-frame').contentDocument.getElementById('agree').checked")
        fullname = t.js("document.getElementById('fullname').value") or ""
        ok = street == "45 Friedrichstrasse" and agree is True and fullname == ""
        return ok, f"street={street!r} agree={agree} fullname_untouched={fullname == ''}"

    return (
        "2. Shadow DOM & Nested Iframe Simultaneous Penetration",
        APP,
        None,
        "Fill ONLY my residential street address to 45 Friedrichstrasse and tick the declaration confirmation checkbox in the frame. Leave name and email untouched, do not submit.",
        {"name": "Kai Student"},
        check,
    )


def task_3_doc_ingest_and_upload():
    """Task 3: Ingest multiple attachments, extract name/email, select category, upload file."""
    def setup_attachments():
        passport = b"PASSPORT\nSurname: ROSTOVA\nGiven Names: ELENA\nDate of Birth: 15 APR 2001\nNo: LR998877\n"
        profile = b"Email: elena@example.com\n"
        for name, data in (("passport.txt", passport), ("profile.txt", profile)):
            post("/api/chat/upload", data=data, headers={"Origin": "chrome-extension://benchmark", "X-File-Name": name})
        post("/api/chat/upload", data=Path("artifacts/passport_scan.pdf").read_bytes(),
             headers={"Origin": "chrome-extension://benchmark", "X-File-Name": "passport_scan.pdf"})

    def check(t, s, p):
        name = t.js("document.getElementById('fullname').value") or ""
        email = t.js("document.getElementById('email').value") or ""
        category = t.js("document.getElementById('category').value") or ""
        file_name = t.js("document.getElementById('passport_file').files[0]?.name") or ""
        ok = "rostova" in name.lower() and email == "elena@example.com" and category == "tourist" and bool(file_name)
        return ok, f"name={name!r} email={email!r} cat={category!r} file={file_name!r}"

    return (
        "3. Document Ingestion, Dropdown Category & Native File Upload",
        APP,
        setup_attachments,
        "From my attached passport and profile, fill my full name, email, select Tourist Visa category, and attach my passport scan file. Do not submit.",
        {},
        check,
    )


def task_4_multiturn_correction():
    """Task 4: Multi-turn memory with mid-flight user correction."""
    def check(t, s, p):
        name = t.js("document.getElementById('fullname').value") or ""
        email = t.js("document.getElementById('email').value") or ""
        category = t.js("document.getElementById('category').value") or ""
        ok = name == "Maya Lindqvist" and email == "maya.new@example.com" and category == "student"
        return ok, f"name={name!r} email={email!r} cat={category!r}"

    return (
        "4. Multi-Turn Memory with Mid-Flight Correction",
        APP,
        "multiturn_correction",
        None,
        {},
        check,
    )


def task_5_live_wikipedia_search():
    """Task 5: Live web search & article navigation on Wikipedia."""
    def check(t, s, p):
        href = t.js("location.href") or ""
        ok = "Ada_Lovelace" in href
        return ok, f"href={href!r}"

    return (
        "5. Live Public Web Search & Disambiguation (Wikipedia)",
        "https://en.wikipedia.org/wiki/Main_Page",
        None,
        "Search Wikipedia for 'Ada Lovelace' and open her article.",
        {},
        check,
    )


TASKS = [task_1_multi_filter, task_2_shadow_and_iframe, task_3_doc_ingest_and_upload,
         task_4_multiturn_correction, task_5_live_wikipedia_search]


def main():
    ensure_daemon()
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else set()

    print("==================================================================")
    print("      JEV ASSIST: 5 HARD END-TO-END BENCHMARK CHALLENGES          ")
    print("==================================================================")

    results = []
    for make in TASKS:
        title, url, setup_fn, message, answers, check = make()
        num_str = title.split(".")[0]
        if only and num_str not in only:
            continue

        print(f"\n--- Running: {title} ---")
        post("/api/chat/history/new")
        time.sleep(1)

        tab = Tab(url if url and not url.startswith("about:") else APP)
        t0 = time.time()
        try:
            if callable(setup_fn):
                setup_fn()
            if setup_fn == "multiturn_correction":
                # Turn 1: establish initial fact
                print("  Turn 1: Setting initial fact...")
                run_chat("Remember my name is Maya Lindqvist and my email is old@example.com. Just say OK, don't do anything yet.", None)
                # Turn 2: correct email and apply to form
                print("  Turn 2: Overriding email and filling form...")
                s, prompts, secs = run_chat(
                    "Actually my email is updated to maya.new@example.com, and I need a Student Visa. "
                    "Fill my full name, email, and choose Student Visa on this form. Do not submit.",
                    APP,
                    answers,
                )
            else:
                s, prompts, secs = run_chat(message, url, answers)

            ok, detail = check(tab, s, prompts)
            steps = sum(len(m.get("steps", [])) for m in s.get("messages", []))
            row = {
                "task": title,
                "passed": bool(ok),
                "seconds": secs if "secs" in locals() else round(time.time() - t0, 1),
                "steps": steps,
                "status": s.get("status"),
                "detail": detail,
            }
        except Exception as exc:
            row = {
                "task": title,
                "passed": False,
                "seconds": round(time.time() - t0, 1),
                "steps": 0,
                "status": "error",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        finally:
            tab.close()

        status_tag = "PASS" if row["passed"] else "FAIL"
        print(f"[{status_tag}] {row['task']} | {row['seconds']}s | {row['steps']} steps | detail: {row['detail']}")
        results.append(row)

    print("\n==================================================================")
    passed_count = sum(1 for r in results if r["passed"])
    print(f"SUMMARY: {passed_count}/{len(results)} Tasks Passed")
    print("==================================================================")
    return results


if __name__ == "__main__":
    main()
