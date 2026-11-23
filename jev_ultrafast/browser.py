"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import os
import re
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
  const re = new RegExp(
    "^(ich lehne ab|alle ablehnen|nur notwendige|nur essenzielle|cookies ablehnen|"
    + "reject all|decline all|reject|decline|disagree|refuse|deny|schließen|close)$",
    "i"
  );
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
            try:
                info = json.loads(urllib.request.urlopen("http://127.0.0.1:9333/json/version", timeout=1).read())
                if "webSocketDebuggerUrl" in info:
                    os.environ["BU_CDP_WS"] = info["webSocketDebuggerUrl"]
            except Exception:
                pass
            ensure_daemon()
            self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
            self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
            try:
                self.call("Target.setAutoAttach", autoAttach=True, waitForDebuggerOnStart=False, flatten=True)
                self.call("Page.enable")
                self.call("DOM.enable")
            except Exception:
                pass
            self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
            # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
            self.call("Emulation.setFocusEmulationEnabled", enabled=True)
            self.call("Page.navigate", url=url)
        self.wait_ready(navigated=bool(url) and url != "about:blank")

    def wait_ready(self, navigated=True, timeout=15):
        """Wait until the new document has rendered something, then until the DOM stops churning.

        A fresh tab starts on about:blank, whose readyState is already "complete". Polling readyState alone let
        heavy sites (Amazon, YouTube, Booking...) be observed as an empty page, and both gears then chose BLOCKED.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                state = self.evaluate(READY_STATE) or {}
            except StalePage:
                state = {}  # mid-navigation: the old document is gone, the new one is not ready yet
            left_blank = not navigated or state.get("href") not in {None, "about:blank"}
            if left_blank and state.get("ready") in {"interactive", "complete"} and state.get("content"):
                break
            time.sleep(0.05)
        self.settle(min(2.5, max(0.0, deadline - time.monotonic())))

    def settle(self, limit=2.5, quiet_ms=300):
        """Resolve once the DOM has had no mutations for quiet_ms, or after limit seconds. Read-only."""
        if limit <= 0:
            return
        try:
            self.call(
                "Runtime.evaluate",
                expression=SETTLE_JS % {"quiet": quiet_ms, "limit": round(limit * 1000)},
                awaitPromise=True,
                returnByValue=True,
            )
        except Exception:
            pass

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
                    }))("""
                    + json.dumps(action)
                    + ")",
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
        page = None
        empty_deadline = time.monotonic() + EMPTY_PAGE_WAIT
        for attempt in range(25):
            try:
                page = browser_operation(
                    {
                        "operation": "observe",
                        "session": self.session,
                        "screenshot": screenshot,
                        "upload": self.allow_upload,
                    }
                )
            except StalePage:
                if attempt == 24:
                    raise
                time.sleep(0.1)
                continue
            # An empty observation is a page still rendering, not a page with no way forward.
            if is_empty(page) and time.monotonic() < empty_deadline:
                self.settle(0.5)
                continue
            return page
        if page is not None:
            return page
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        if action is not None and action.get("kind") == "wait":
            return True  # Waiting mutates nothing, so a changed page cannot make it unsafe.
        # Check only the target node and form state, not the whole page: ads and carousels that keep changing
        # elsewhere used to make every decision stale, and the agent re-predicted until its call budget ran out.
        if action is not None and action.get("kind") in {"click", "select", "coords", "fill", "upload"}:
            node = action.get("node")
            if node is not None and type(node) is int:
                current = self.evaluate(
                    "(() => { const c=window.__jevFast; "
                    f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
                )
                return current == [page["page_key"], page["guards"].get(str(node))]
            if action.get("kind") == "coords":
                return True
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


EMPTY_PAGE_WAIT = 6.0

READY_STATE = """(() => ({
  href: location.href,
  ready: document.readyState,
  content: !!document.body && ((document.body.innerText || '').trim().length > 0 ||
    !!document.querySelector('a[href],button,input,select,textarea,[role="button"],[role="link"]'))
}))()"""

SETTLE_JS = """new Promise(resolve => {
  let timer;
  const done = () => { observer.disconnect(); clearTimeout(cap); resolve(true); };
  const observer = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(done, %(quiet)d); });
  observer.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  timer = setTimeout(done, %(quiet)d);
  const cap = setTimeout(done, %(limit)d);
})"""


def is_empty(page):
    """No text and nothing to act on besides WAIT: the document has not rendered yet."""
    actionable = [a for a in page.get("actions", []) if a.get("kind") != "wait"]
    return not (page.get("text") or "").strip() and not actionable


# Human-verification interstitials (Cloudflare, Amazon, Google, generic CAPTCHAs). The agent must never try to solve
# them; it waits briefly (some clear by themselves) and then asks the person instead of wandering off.
BOT_WALL = re.compile(
    r"just a moment\.\.\.|verify(ing)? you are (a )?human|are you a robot|not a robot|robot check|"
    r"checking (if the site connection is secure|your browser)|attention required.{0,4}cloudflare|"
    r"enter the characters you see|type the characters you see|unusual traffic from your computer|"
    r"press (and|&) hold|complete the security check|security verification|captcha",
    re.I,
)


def bot_wall(page):
    """The matched phrase when the page is a human check rather than content, else None.

    Short pages only: a long article that mentions CAPTCHAs is not a wall."""
    text = page.get("text") or ""
    if len(text) > 2500:
        return None
    match = BOT_WALL.search(f"{page.get('title') or ''}\n{text}")
    return match.group(0) if match else None


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
            target = evaluate(
                """(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              const r=window.__jevFast.box(e);
              const vl=Math.max(r.x,0), vr=Math.min(r.x+r.w,innerWidth);
              const vt=Math.max(r.y,0), vb=Math.min(r.y+r.h,innerHeight);
              if (vr<=vl || vb<=vt) return null;
              const x=(vl+vr)/2, y=(vt+vb)/2;
              // Hit-test inside the element's own shadow root or frame document at visible coordinates.
              const root=e.getRootNode();
              const hit=(root.elementFromPoint ? root : e.ownerDocument).elementFromPoint(x, y);
              if (hit && !e.contains(hit) && !hit.contains(e)) return null;
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              return {x,y};
            })("""
                + json.dumps(action)
                + ")"
            )
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                x, y = target["x"], target["y"]
                call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
                time.sleep(0.08)
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
                    call("Input.dispatchKeyEvent", type="rawKeyDown", key="Backspace", code="Backspace", windowsVirtualKeyCode=8)
                    call("Input.dispatchKeyEvent", type="keyUp", key="Backspace", code="Backspace", windowsVirtualKeyCode=8)
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")

    # Multi-frame traversal: discover OOPIFs and embedded child frames (Azure blades, payment frames, OAuth)
    if info.get("has_iframes"):
        try:
            ft = call("Page.getFrameTree")
            child_frames = ft.get("frameTree", {}).get("childFrames", [])
            if child_frames:
                main_actions = list(info.get("actions", []))
                base_node_offset = 10000
                for c_idx, cf in enumerate(child_frames):
                    f_info = cf.get("frame", {})
                    fid = f_info.get("id")
                    if not fid:
                        continue
                    try:
                        owner = call("DOM.getFrameOwner", frameId=fid)
                        bnid = owner.get("backendNodeId")
                        if not bnid:
                            continue
                        box = call("DOM.getBoxModel", backendNodeId=bnid)
                        content = box.get("model", {}).get("content", [])
                        if len(content) < 2:
                            continue
                        frame_ox, frame_oy = content[0], content[1]

                        # Create isolated execution context in child frame and execute READ_STATE
                        world = call("Page.createIsolatedWorld", frameId=fid, worldName="jev_frame_eval")
                        cid = world.get("executionContextId")
                        if not cid:
                            continue
                        c_eval = call("Runtime.evaluate", expression=READ_STATE, contextId=cid, returnByValue=True)
                        c_info = c_eval.get("result", {}).get("value")
                        if not c_info or not c_info.get("actions"):
                            continue

                        # Translate child frame coordinates to global viewport pixels
                        for act in c_info["actions"]:
                            act_node = act.get("node")
                            if isinstance(act_node, int):
                                act["node"] = base_node_offset * (c_idx + 1) + act_node
                            if "rect" in act and isinstance(act["rect"], dict):
                                act["rect"]["x"] += frame_ox
                                act["rect"]["y"] += frame_oy
                            main_actions.append(act)

                        # Append frame text content
                        if c_info.get("text"):
                            info["text"] = (info.get("text", "") + "\n\n" + c_info["text"])[:16000]
                    except Exception:
                        continue
                info["actions"] = main_actions
        except Exception:
            pass

    if not request.get("upload"):
        # Without user files there is nothing to attach; never offer the operation.
        info["actions"] = [a for a in info["actions"] if a["kind"] != "upload"]
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info
