"""Live verification script for student residence permit renewal on leipzig.de."""

import os
import sys

# Ensure immediate unbuffered console output
sys.stdout.reconfigure(line_buffering=True)

from jev_ultrafast.planner import run

user_interactions = []

def interactive_dialog(prompt):
    user_interactions.append(prompt)
    print(f"\n[INTERACTIVE USER QUESTION]: {prompt}", flush=True)
    if "Postleitzahl" in prompt or "PLZ" in prompt:
        print("  -> User provided: 04109", flush=True)
        return "04109"
    if "Allow?" in prompt:
        print("  -> User safety confirmation: N (do not submit)", flush=True)
        return "n"
    print("  -> User provided: Leipzig University Student", flush=True)
    return "Leipzig University Student"

goal = (
    "i am a student in leipzig i wanna renew my residence permit please go to the form fill it for me "
    "stop and ask me if u need anything ."
)

print(f"Goal: {goal}\nStarting run on leipzig.de...\n", flush=True)

res = run(
    goal,
    url="https://www.leipzig.de/service-portal/dienstleistung/aufenthaltserlaubnis-zum-studium-beantragen-5f1ae6c5debf6",
    ask_user=interactive_dialog,
    rounds=1,
)

print("\n=== RUN COMPLETED ===", flush=True)
print(f"Final Page URL: {res['page']['url']}", flush=True)
print(f"User questions prompted: {len(user_interactions)}", flush=True)
for q in user_interactions:
    print(f"  - {q}", flush=True)
print("\nExecution History:")
for h in res["history"]:
    print(f"  Step {h['step']}: [Gear {h.get('gear', 1)}] {h['action']} ({h['kind']}) text={h.get('text')}")
