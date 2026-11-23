"""Site benchmark: run each task in benchmarks/sites.json through Agent in Brave, one at a time.

Usage:  uv run --env-file .env python benchmarks/run_sites.py [--only id,id] [--timeout 240] [--mode agent|planner]
Each task runs in its own child process (crash/hang isolation). Sensitive clicks are auto-denied.
Results go to benchmarks/results/<stamp>-<mode>/ (results.jsonl, stop_log.jsonl, summary.md).
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITES = ROOT / "sites.json"
LAYA = os.environ.get("GEAR2_LAYA_URL", "http://127.0.0.1:8793").rstrip("/")


def check_services():
    """Laya decision server and OmniRoute must answer before we start. Returns a list of problems."""
    problems = []
    for name, url in [("laya", f"{LAYA}/v1/systemone"), ("omniroute", "http://127.0.0.1:20128/")]:
        try:
            urllib.request.urlopen(url, timeout=3)
        except urllib.error.HTTPError:
            pass  # any HTTP answer means the server is up
        except Exception as e:
            problems.append(f"{name} not reachable at {url}: {e}")
    return problems


def judge(task, url, text):
    """text = visible page text plus the serialized action list (which carries form field values)."""
    s = task.get("success", {})
    url, low = url or "", (text or "").lower()
    ok = all(x in url for x in s.get("url_contains", []))
    if s.get("url_any"):
        ok = ok and any(x in url for x in s["url_any"])
    if s.get("text_any"):
        ok = ok and any(x.lower() in low for x in s["text_any"])
    if s.get("text_all"):
        ok = ok and all(x.lower() in low for x in s["text_all"])
    return ok


def run_one(task, mode="agent"):
    """Child process: drive one task, print one JSON line on stdout."""
    denied, asked, reports = [], [], []

    def approve(action):
        denied.append(action.get("label"))
        return False

    def ask(prompt):
        if "Allow?" in prompt:
            denied.append(prompt)  # sensitive click: auto-deny
            return "n"
        asked.append(prompt)  # MissingValue question: no profile in the benchmark
        return "skip"

    t0 = time.perf_counter()
    out = {"id": task["id"], "denied_clicks": denied}
    try:
        if mode == "planner":
            from jev_ultrafast import planner
            st = planner.run(task["goal"], url=task["start_url"], ask_user=ask, report=lambda *a: reports.append(" ".join(map(str, a))))
        else:
            from jev_ultrafast.agent import Agent, MissingValue
            with Agent(task["start_url"], task["goal"], profile={}, approve=approve) as agent:
                try:
                    # The agent's own MAX_STEPS limit applies; the parent's wall-clock timeout catches hangs.
                    for _ in agent.run():
                        pass
                except MissingValue as m:
                    agent.stop("blocked", "bench_missing_value", f"form asked for '{m.field}' (benchmark gives no profile)")
                st = agent.state
        page = st.get("page") or {}
        stop = st.get("stop_reason") or {}
        code, message = stop.get("code"), stop.get("message")
        if mode == "planner" and code is None:
            code = "verified_done" if st.get("done") else "verify_not_done"
            message = st.get("missing")
        text = page.get("text") or ""
        out.update(status=st["status"], code=code, message=message, steps=len(st["history"]),
                   url=page.get("url"), title=page.get("title"), last_action=stop.get("last_action"),
                   text_head=text[:400], text=text[:16000])
        if mode == "planner":
            out.update(verified_done=st.get("done"), missing=st.get("missing"), asked=asked,
                       report=[r for r in reports if not r.lstrip()[:1].isdigit()][-40:])
        haystack = text + "\n" + json.dumps(page.get("actions") or [], ensure_ascii=False)
        out["pass"] = judge(task, page.get("url"), haystack)
        out["agent_said_done"] = st["status"] == "done"
    except Exception as e:
        out.update(status="crashed", code="bench_exception", message=f"{type(e).__name__}: {e}", **{"pass": False})
    out["elapsed_s"] = round(time.perf_counter() - t0, 1)
    print("@@RESULT " + json.dumps(out), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only", help="comma-separated task ids")
    p.add_argument("--timeout", type=int, default=300, help="wall-clock seconds per task")
    p.add_argument("--mode", choices=["agent", "planner"], default="agent", help="driving loop to benchmark")
    p.add_argument("--max-429", type=int, default=3, help="abort the run after this many HTTP 429 crashes")
    p.add_argument("--child", help=argparse.SUPPRESS)
    a = p.parse_args()
    tasks = json.loads(SITES.read_text(encoding="utf-8"))["tasks"]

    if a.child:
        run_one(next(t for t in tasks if t["id"] == a.child), a.mode)
        return

    os.environ.setdefault("BROWSER", "brave")
    os.environ.setdefault("TYPESAFE_BASE_URL", "http://127.0.0.1:8793")
    os.environ.setdefault("TYPESAFE_API_KEY", "local")
    os.environ.setdefault("GEAR2_LAYA_URL", "http://127.0.0.1:8793")
    problems = check_services()
    if problems:
        sys.exit("Services not up, not running:\n  " + "\n  ".join(problems))

    if a.only:
        keep = set(a.only.split(","))
        tasks = [t for t in tasks if t["id"] in keep]
    out_dir = ROOT / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}-{a.mode}"
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["JEV_STOP_LOG"] = str(out_dir / "stop_log.jsonl")
    results = []
    for i, t in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {t['id']} ...", flush=True)
        t0 = time.perf_counter()
        try:
            cp = subprocess.run([sys.executable, __file__, "--child", t["id"], "--mode", a.mode],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=a.timeout, cwd=ROOT.parent)
            line = next((l for l in cp.stdout.splitlines() if l.startswith("@@RESULT ")), None)
            r = json.loads(line[9:]) if line else {
                "id": t["id"], "status": "crashed", "code": "bench_no_result", "pass": False,
                "message": (cp.stderr or cp.stdout)[-600:]}
        except subprocess.TimeoutExpired as e:
            r = {"id": t["id"], "status": "timeout", "code": "bench_timeout", "pass": False,
                 "message": f"no result within {a.timeout}s", "tail": str(e.stdout or "")[-400:]}
        r.setdefault("elapsed_s", round(time.perf_counter() - t0, 1))
        r["area"], r["start_url"] = t.get("area"), t["start_url"]
        results.append(r)
        if sum("HTTP 429" in str(x.get("message")) for x in results) > a.max_429:
            print(f"ABORT: more than {a.max_429} tasks crashed on HTTP 429", flush=True)
            with open(out_dir / "results.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(r) + chr(10))
            break
        with open(out_dir / "results.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")
        print(f"    {'PASS' if r.get('pass') else 'FAIL'} {r.get('code')} steps={r.get('steps')} "
              f"{r.get('elapsed_s')}s {r.get('url')}", flush=True)

    rows = ["| id | pass | code | steps | s | final url | message |", "|---|---|---|---|---|---|---|"]
    for r in results:
        msg = str(r.get("message") or "").replace("|", "/").replace("\n", " ")[:120]
        rows.append(f"| {r['id']} | {'✅' if r.get('pass') else '❌'} | {r.get('code')} | {r.get('steps')} | "
                    f"{r.get('elapsed_s')} | {r.get('url')} | {msg} |")
    n = sum(bool(r.get("pass")) for r in results)
    (out_dir / "summary.md").write_text(f"# Site benchmark {out_dir.name}\n\n{n}/{len(results)} passed\n\n"
                                        + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"\n{n}/{len(results)} passed -> {out_dir}")


if __name__ == "__main__":
    main()
