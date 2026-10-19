"""Live demonstration of Vision-Laya on a real web page with window focus."""

import json
import os
import sys
import time
from pathlib import Path

# Load .env
env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

os.environ["TYPESAFE_BASE_URL"] = "http://127.0.0.1:8792"
os.environ["GEAR2_BACKEND"] = "laya"
os.environ["GEAR2_LAYA_URL"] = "http://127.0.0.1:8792"
os.environ["PYTHONUTF8"] = "1"

from jev_ultrafast import Agent
from browser_harness.helpers import cdp

url = "https://news.ycombinator.com/"
goal = "Click the comments link on the first story to open the discussion thread"

print("=" * 65)
print("LIVE VISION-LAYA DEMO")
print(f"URL: {url}")
print(f"Goal: {goal}")
print(f"Model: Vision-Laya local server (http://127.0.0.1:8792)")
print("=" * 65)

with Agent(url, goal) as agent:
    # Focus and bring Chrome window to the front
    try:
        cdp("Target.activateTarget", targetId=agent.browser.target)
        agent.browser.call("Page.bringToFront")
        # On Windows, bring Chrome to foreground via PowerShell AppActivate
        os.system('powershell -Command "(New-Object -ComObject WScript.Shell).AppActivate(\'Chrome\')" > $null 2>&1')
        print("[Browser window focused and brought to front]")
    except Exception as e:
        print(f"[Focus note: {e}]")

    time.sleep(1) # Let user see initial page
    step_num = 0

    for state in agent.run():
        step_num += 1
        d = state["decisions"][-1] if state.get("decisions") else {}
        history = state.get("history", [])
        last_action = history[-1] if history else {}

        gear = d.get("gear", 1)
        op = d.get("operation")
        choice = d.get("choice")
        lat = d.get("latency_ms", 0)
        conf = d.get("confidence", 0)
        coords = f"({d.get('x')}, {d.get('y')})" if d.get("x") is not None else "None"

        print(f"\nStep {step_num}:")
        print(f"  - Gear: {gear} ({'Vision-Laya Multimodal' if gear == 2 else 'Fast DOM Policy'})")
        print(f"  - Operation: {op} -> Target: {choice} {coords}")
        print(f"  - Confidence: {conf:.3f} | Latency: {lat}ms")
        if last_action:
            print(f"  - Executed: {last_action.get('action')}")

        if state.get("status") in {"done", "blocked"} or step_num >= 5:
            break

    time.sleep(1)
    final_snap = agent.snapshot()
    print("\n" + "=" * 65)
    print(f"FINAL RESULT: Status={final_snap['status']}")
    print(f"Final URL: {final_snap['page']['url']}")
    print(f"Final Title: {final_snap['page']['title']}")
    print("=" * 65)

    # Save final screenshot for visual inspection
    shot_bytes = agent.browser.call("Page.captureScreenshot", format="png")
    import base64
    out_img = Path("artifacts/live_demo_result.png")
    out_img.write_bytes(base64.b64decode(shot_bytes["data"]))
    print(f"Saved live screenshot to {out_img}")
