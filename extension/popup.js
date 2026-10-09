const BASE = "http://127.0.0.1:8766";
let currentTabUrl = "";
let isPolling = false;

// DOM elements
const serverDot = document.getElementById("server-dot");
const serverText = document.getElementById("server-text");
const tabUrlEl = document.getElementById("tab-url");
const lockTabCb = document.getElementById("lock-tab-cb");
const taskInput = document.getElementById("task-input");
const docsInput = document.getElementById("docs-input");
const filesInput = document.getElementById("files-input");
const startBtn = document.getElementById("start-btn");
const stopBtn = document.getElementById("stop-btn");
const optionsToggle = document.getElementById("options-toggle");
const optionsPanel = document.getElementById("options-panel");
const promptCard = document.getElementById("user-prompt-card");
const promptQuestion = document.getElementById("prompt-question");
const textInputRow = document.getElementById("text-input-row");
const approvalInputRow = document.getElementById("approval-input-row");
const userAnswerInput = document.getElementById("user-answer-input");
const sendAnswerBtn = document.getElementById("send-answer-btn");
const approveBtn = document.getElementById("approve-btn");
const denyBtn = document.getElementById("deny-btn");
const statusPill = document.getElementById("status-pill");
const streamList = document.getElementById("stream-list");

// Toggle advanced options
optionsToggle.addEventListener("click", () => {
  const isHidden = optionsPanel.style.display === "none" || !optionsPanel.style.display;
  optionsPanel.style.display = isHidden ? "flex" : "none";
  optionsToggle.textContent = isHidden ? "▼ Advanced (Documents & Uploads)" : "▶ Advanced (Documents & Uploads)";
});

// Detect current browser tab
let currentTabId = null;
if (typeof chrome !== "undefined" && chrome.tabs) {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (tabs && tabs[0]) currentTabId = tabs[0].id;
    if (tabs && tabs[0] && tabs[0].url) {
      currentTabUrl = tabs[0].url;
      if (lockTabCb && lockTabCb.checked) {
        tabUrlEl.textContent = currentTabUrl;
      }
    }
  });
} else {
  currentTabUrl = "http://127.0.0.1:8766/application.html";
}

if (lockTabCb) {
  lockTabCb.addEventListener("change", () => {
    tabUrlEl.textContent = lockTabCb.checked
      ? (currentTabUrl || "Current active tab")
      : "Auto-detected by AI from your goal ✨";
  });
}

// Start Task
startBtn.addEventListener("click", async () => {
  const request = taskInput.value.trim();
  if (!request) {
    alert("Please enter a task description!");
    return;
  }
  const docs = docsInput.value.split(",").map(d => d.trim()).filter(Boolean);
  const files = filesInput.value.split(",").map(f => f.trim()).filter(Boolean);
  const url = (lockTabCb && lockTabCb.checked && currentTabUrl && !currentTabUrl.startsWith("chrome://") && !currentTabUrl.startsWith("brave://"))
    ? currentTabUrl
    : null;

  if (currentTabUrl.startsWith("brave://") || currentTabUrl.startsWith("chrome://")) {
    alert("Browser pages (brave://, chrome://) can't be automated. Open any normal website tab first.");
    return;
  }

  try {
    startBtn.disabled = true;
    startBtn.textContent = "Attaching to this tab...";
    // The background worker attaches the debugger to this tab and connects it to the local agent.
    const attached = await chrome.runtime.sendMessage({ type: "attach", tabId: currentTabId });
    if (!attached || !attached.ok) {
      throw new Error((attached && attached.error) || "Could not attach to this tab");
    }
    startBtn.textContent = "Launching...";
    const res = await fetch(`${BASE}/api/assist/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request, docs, files, url })
    });
    const data = await res.json();
    if (data.status === "started") {
      startBtn.style.display = "none";
      stopBtn.style.display = "block";
    } else {
      alert("Error: " + (data.error || "Could not start task"));
      startBtn.disabled = false;
      startBtn.textContent = "Start Task 🚀";
    }
  } catch (err) {
    alert("Could not start: " + (err && err.message ? err.message : "local agent on 127.0.0.1:8766 is not running"));
    chrome.runtime.sendMessage({ type: "release" });
    startBtn.disabled = false;
    startBtn.textContent = "Start Task 🚀";
  }
});

// Stop Task
stopBtn.addEventListener("click", async () => {
  try {
    await fetch(`${BASE}/api/assist/stop`, { method: "POST" });
  } catch (err) {}
  chrome.runtime.sendMessage({ type: "release" });
});

// Respond to Interactive Prompts
async function sendResponse(answer) {
  try {
    promptCard.style.display = "none";
    // Show immediate chat feedback in the stream list
    const chatBubble = document.createElement("div");
    chatBubble.className = "stream-item";
    chatBubble.style.cssText = "border-left: 3px solid #3b82f6; background: rgba(59, 130, 246, 0.15);";
    chatBubble.innerHTML = `
      <span class="stream-time">💬</span>
      <span class="stream-msg" style="color: #93c5fd; font-weight: 600;">You: ${escapeHtml(answer)}</span>
    `;
    streamList.appendChild(chatBubble);
    streamList.scrollTop = streamList.scrollHeight;

    await fetch(`${BASE}/api/assist/respond`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ response: answer })
    });
    userAnswerInput.value = "";
  } catch (err) {
    alert("Error sending answer to agent: " + err);
  }
}

sendAnswerBtn.addEventListener("click", () => {
  const val = userAnswerInput.value.trim();
  if (val) sendResponse(val);
});
userAnswerInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const val = userAnswerInput.value.trim();
    if (val) sendResponse(val);
  }
});

approveBtn.addEventListener("click", () => sendResponse("y"));
denyBtn.addEventListener("click", () => sendResponse("n"));

// Poll State every 500ms
async function pollState() {
  try {
    const res = await fetch(`${BASE}/api/assist/state`);
    const state = await res.json();

    serverDot.className = "dot online";
    serverText.textContent = "Online";

    // Update status pill
    statusPill.className = `pill ${state.status}`;
    statusPill.textContent = state.status.replace("_", " ").toUpperCase();

    // Toggle start/stop button
    if (state.status === "running" || state.status === "waiting_user") {
      startBtn.style.display = "none";
      stopBtn.style.display = "block";
    } else {
      startBtn.style.display = "block";
      startBtn.disabled = false;
      startBtn.textContent = "Start Task 🚀";
      stopBtn.style.display = "none";
    }

    // Handle user prompt (waiting_user)
    if (state.status === "waiting_user" && state.pending_prompt) {
      promptCard.style.display = "block";
      promptQuestion.textContent = state.pending_prompt;
      if (state.is_approval) {
        textInputRow.style.display = "none";
        approvalInputRow.style.display = "flex";
      } else {
        textInputRow.style.display = "flex";
        approvalInputRow.style.display = "none";
        userAnswerInput.focus();
      }
    } else {
      promptCard.style.display = "none";
    }

    // Render Stream logs
    if (state.history && state.history.length > 0) {
      let items = state.history.map(item => `
        <div class="stream-item">
          <span class="stream-time">${item.time}</span>
          <span class="stream-msg">${escapeHtml(item.msg)}</span>
        </div>
      `).join("");
      if (state.error) {
        items += `
          <div class="stream-item" style="border-left: 3px solid var(--danger); background: rgba(239, 68, 68, 0.15);">
            <span class="stream-time">⚠️</span>
            <span class="stream-msg" style="color: #fca5a5;">${escapeHtml(state.error)}</span>
          </div>`;
      }
      streamList.innerHTML = items;
      streamList.scrollTop = streamList.scrollHeight;
    } else if (state.error) {
      streamList.innerHTML = `
        <div class="stream-item" style="border-left: 3px solid var(--danger); background: rgba(239, 68, 68, 0.15);">
          <span class="stream-time">⚠️</span>
          <span class="stream-msg" style="color: #fca5a5;">${escapeHtml(state.error)}</span>
        </div>`;
    } else if (state.status === "idle") {
      streamList.innerHTML = `<div class="empty-state">No active task. Enter a goal above and click Start.</div>`;
    }

  } catch (err) {
    serverDot.className = "dot";
    serverText.textContent = "Offline";
  }
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

setInterval(pollState, 600);
pollState();
