"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text(encoding="utf-8")
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"

DISMISS_COOKIES_JS = """(() => {
  const re = /^(ich lehne ab|alle ablehnen|nur notwendige|nur essenzielle|cookies ablehnen|reject all|decline all|reject|decline|disagree|refuse|deny|schließen|close)$/i;
  const buttons = [...document.querySelectorAll('button, a, [role="button"]')];
  for (const b of buttons) {
    if (re.test(b.innerText?.trim() || "") && b.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) {
      b.click();
      return true;
    }
  }
  const selectors = [
    'button[id*="reject" i]', 'button[id*="decline" i]', 'button[id*="deny" i]',
    'button[class*="reject" i]', 'button[class*="decline" i]',
    '#didomi-notice-agree-button', '#onetrust-reject-all-handler',
    '.cc-dismiss', '.cookie-banner button', 'button[data-testid*="cookie" i]'
  ];
  for (const s of selectors) {
    const el = document.querySelector(s);
    if (el && el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) {
      el.click();
      return true;
    }
  }
  return false;
})()"""

# Common paths across Windows, macOS, and Linux. Order: Brave first if requested, Chrome first otherwise.
CANDIDATES = {
    "brave": [
        os.environ.get("BRAVE_PATH"),
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        shutil.which("brave-browser"),
        shutil.which("brave"),
    ],
    "chrome": [
        os.environ.get("CHROME_PATH"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        shutil.which("chromium"),
    ],
}


def find_browser(preference=None):
    pref = (preference or os.environ.get("BROWSER") or "").lower()
    order = ("brave", "chrome") if pref == "brave" else ("chrome", "brave")
    for key in order:
        for path in CANDIDATES[key]:
            if path and os.path.isfile(path):
                return path, key
    return None, None


def ensure_browser(port=9333, preference=None):
    """Start an isolated debug browser if the CDP port is not already answering."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
        return
    except Exception:
        pass
    exe, kind = find_browser(preference)
    if not exe:
        return
    profile = Path.home() / f".jev-{kind}"
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1300,900",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        time.sleep(0.15)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
            return
        except Exception:
            continue


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class Browser:
    allow_upload = False

    def __init__(self, url):
        global cdp
        if os.environ.get("JEV_DRIVER") == "extension":
            # The user's own tab in their real browser, driven through the Jev Assist extension's debugger session.
            from . import relay

            cdp = relay.cdp
            # target stays None so close() never closes the user's own tab.
            self.target, self.session = None, "extension"
            try:
                self.call("Emulation.setFocusEmulationEnabled", enabled=True)
            except Exception:
                pass
            if url and url != self.evaluate("location.href"):
                self.call("Page.navigate", url=url)
                time.sleep(0.3)  # let the old document unload before polling readyState
        else:
            ensure_browser()
            ensure_daemon()
            self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
            self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
            self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
            # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
            self.call("Emulation.setFocusEmulationEnabled", enabled=True)
            self.call("Page.navigate", url=url)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if self.evaluate("document.readyState") == "complete":
                    break
            except StalePage:
                pass  # mid-navigation: the old document is gone, the new one is not ready yet
            time.sleep(0.02)

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        # Auto-dismiss cookie walls and consent overlays
        try:
            if self.evaluate(DISMISS_COOKIES_JS):
                time.sleep(0.15)
        except Exception:
            pass
        for attempt in range(25):
            try:
                return browser_operation(
                    {"operation": "observe", "session": self.session, "screenshot": screenshot,
                     "upload": self.allow_upload}
                )
            except StalePage:
                if attempt == 24:
                    raise
                time.sleep(0.1)
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            time.sleep(0.1)
        result = browser_operation({"operation": "act", "session": self.session, "action": action, "text": text})
        self.after_input = action if action["kind"] != "wait" else None
        return result

    def close(self):
        if self.target:
            cdp("Target.closeTarget", targetId=self.target)
            self.target = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def evaluate(expression):
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            call("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=650, deltaX=0, deltaY=action["delta"])
        elif kind == "coords":
            x, y = int(action["x"]), int(action["y"])
            call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            time.sleep(0.04)
            for event in ("mousePressed", "mouseReleased"):
                call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
            return {"executed": "coords"}
        elif kind == "upload":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            node = call(
                "Runtime.evaluate",
                expression=f"(e => e?.isConnected && e.type==='file' && !e.disabled ? e : null)"
                f"(window.__jevFast?.nodes.get({action['node']}))",
            ).get("result", {})
            if node.get("subtype") != "node":
                raise StalePage("File input changed. Observe again.")
            # The path comes from the user's own file list (Agent.files), never from model text.
            call("DOM.setFileInputFiles", files=[request["text"]], objectId=node["objectId"])
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            target = evaluate("""(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              const r=window.__jevFast.box(e), x=r.x+r.w/2, y=r.y+r.h/2;
              if (!r.w || !r.h || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              // Hit-test inside the element's own shadow root or frame document, at its local coordinates.
              const local=e.getBoundingClientRect(), root=e.getRootNode();
              const hit=(root.elementFromPoint ? root : e.ownerDocument)
                .elementFromPoint(local.x+local.width/2, local.y+local.height/2);
              if (!e.contains(hit)) return null;
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              return {x,y};
            })(""" + json.dumps(action) + ")")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                        commands=["selectAll"],
                    )
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                    )
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    if not request.get("upload"):
        # Without user files there is nothing to attach; never offer the operation.
        info["actions"] = [a for a in info["actions"] if a["kind"] != "upload"]
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info
