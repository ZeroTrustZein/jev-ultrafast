// Holds the debugger session on the user's tab and relays DevTools commands from the local agent.
// Lives in the service worker because the popup closes whenever the user clicks back into the page.

const RELAY = "ws://127.0.0.1:8767";
let socket = null;
let tabId = null;

function debuggee() {
  return { tabId };
}

async function attach(id) {
  if (tabId === id) return;
  if (tabId !== null) await detach();
  await chrome.debugger.attach({ tabId: id }, "1.3");
  tabId = id;
  try {
    await chrome.debugger.sendCommand(debuggee(), "Emulation.setFocusEmulationEnabled", { enabled: true });
  } catch (_) {}
}

async function detach() {
  if (tabId === null) return;
  const id = tabId;
  tabId = null;
  try {
    await chrome.debugger.detach({ tabId: id });
  } catch (_) {}
}

function connect() {
  return new Promise((resolve, reject) => {
    if (socket && socket.readyState === WebSocket.OPEN) return resolve();
    socket = new WebSocket(RELAY);
    socket.onopen = () => resolve();
    socket.onerror = () => reject(new Error("Local agent relay not reachable on 127.0.0.1:8767"));
    socket.onclose = () => {
      socket = null;
      detach();
    };
    socket.onmessage = async (event) => {
      const { id, method, params } = JSON.parse(event.data);
      let reply;
      try {
        if (tabId === null) throw new Error("No tab attached");
        const result = await chrome.debugger.sendCommand(debuggee(), method, params || {});
        reply = { id, result: result || {} };
      } catch (err) {
        reply = { id, error: String(err && err.message ? err.message : err) };
      }
      if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(reply));
    };
  });
}

// Navigation inside the tab keeps the session; only the user closing the tab or the debugger bar ends it.
chrome.debugger.onDetach.addListener((source) => {
  if (source.tabId === tabId) {
    tabId = null;
    if (socket) socket.close();
  }
});
chrome.tabs.onRemoved.addListener((id) => {
  if (id === tabId) {
    tabId = null;
    if (socket) socket.close();
  }
});

// Release the tab once the agent finishes, so the "debugging this browser" bar never outlives a task.
let sawRunning = false;
setInterval(async () => {
  if (tabId === null) {
    sawRunning = false;
    return;
  }
  try {
    const state = await (await fetch("http://127.0.0.1:8766/api/assist/state")).json();
    if (["thinking", "running", "waiting_user"].includes(state.status)) {
      sawRunning = true;
    } else if (sawRunning) {
      sawRunning = false;
      if (socket) socket.close();
      await detach();
    }
  } catch (_) {}
}, 2000);

// Clicking the extension toolbar icon opens the persistent side panel instead of a temporary popup.
if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
} else {
  chrome.action.onClicked.addListener(async (tab) => {
    if (chrome.sidePanel && chrome.sidePanel.open) {
      await chrome.sidePanel.open({ windowId: tab.windowId });
    }
  });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    try {
      if (message.type === "attach") {
        await attach(message.tabId);
        await connect();
        sendResponse({ ok: true });
      } else if (message.type === "release") {
        if (socket) socket.close();
        await detach();
        sendResponse({ ok: true });
      } else {
        sendResponse({ ok: false, error: "Unknown message" });
      }
    } catch (err) {
      await detach();
      sendResponse({ ok: false, error: String(err && err.message ? err.message : err) });
    }
  })();
  return true;
});
