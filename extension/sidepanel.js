const BASE = "http://127.0.0.1:8766";
let currentTabUrl = "";
let currentTabId = null;
let attachedFiles = [];
let lastMessageCount = 0;

// DOM elements
const chatContainer = document.getElementById("chat-container");
const welcomeCard = document.getElementById("welcome-card");
const contextUrlEl = document.getElementById("context-url");
const lockTabCb = document.getElementById("lock-tab-cb");
const composerInput = document.getElementById("composer-input");
const sendBtn = document.getElementById("send-btn");
const stopBtn = document.getElementById("stop-btn");
const resetBtn = document.getElementById("reset-btn");
const filePicker = document.getElementById("file-picker");
const attachmentsList = document.getElementById("attachments-list");
const serverDot = document.getElementById("server-dot");
const serverText = document.getElementById("server-text");

// 1. Detect current browser tab
if (typeof chrome !== "undefined" && chrome.tabs) {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (tabs && tabs[0]) {
      currentTabId = tabs[0].id;
      if (tabs[0].url) {
        currentTabUrl = tabs[0].url;
        contextUrlEl.textContent = currentTabUrl;
      }
    }
  });
}

// 2. Auto-grow composer textarea
composerInput.addEventListener("input", () => {
  composerInput.style.height = "auto";
  composerInput.style.height = Math.min(composerInput.scrollHeight, 120) + "px";
  if (composerInput.value.trim()) {
    sendBtn.classList.add("active");
  } else {
    sendBtn.classList.remove("active");
  }
});

// 3. Quick Chips
document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    composerInput.value = chip.getAttribute("data-prompt");
    composerInput.dispatchEvent(new Event("input"));
    sendMessage();
  });
});

// 4. File attachments
filePicker.addEventListener("change", (e) => {
  for (const f of e.target.files) {
    if (!attachedFiles.includes(f.name)) {
      attachedFiles.push(f.name);
    }
  }
  renderAttachments();
});

function renderAttachments() {
  attachmentsList.innerHTML = attachedFiles.map((name, i) => `
    <div class="att-chip">
      <span>📄 ${escapeHtml(name)}</span>
      <span class="rm" data-idx="${i}">×</span>
    </div>
  `).join("");
  document.querySelectorAll(".att-chip .rm").forEach((rm) => {
    rm.addEventListener("click", () => {
      attachedFiles.splice(parseInt(rm.getAttribute("data-idx")), 1);
      renderAttachments();
    });
  });
}

// 5. Send Message
async function sendMessage() {
  const text = composerInput.value.trim();
  if (!text) return;

  composerInput.value = "";
  composerInput.style.height = "auto";
  sendBtn.classList.remove("active");
  if (welcomeCard) welcomeCard.style.display = "none";

  // If driving this tab, attach debugger if not already attached
  if (typeof chrome !== "undefined" && chrome.runtime) {
    try {
      chrome.runtime.sendMessage({ type: "attach", tabId: currentTabId });
    } catch (_) {}
  }

  const url = (lockTabCb && lockTabCb.checked && currentTabUrl && !currentTabUrl.startsWith("brave://") && !currentTabUrl.startsWith("chrome://"))
    ? currentTabUrl
    : null;

  try {
    await fetch(`${BASE}/api/chat/send`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, files: attachedFiles, url })
    });
  } catch (err) {
    alert("Could not connect to Jev local server on 127.0.0.1:8766. Is the server running?");
  }
}

sendBtn.addEventListener("click", sendMessage);
composerInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// 6. Stop & Reset
stopBtn.addEventListener("click", async () => {
  try {
    await fetch(`${BASE}/api/chat/stop`, { method: "POST" });
  } catch (_) {}
});

resetBtn.addEventListener("click", async () => {
  try {
    await fetch(`${BASE}/api/chat/reset`, { method: "POST" });
    chatContainer.innerHTML = "";
    if (welcomeCard) chatContainer.appendChild(welcomeCard);
    welcomeCard.style.display = "block";
    lastMessageCount = 0;
  } catch (_) {}
});

// 7. Poll State
async function pollChat() {
  try {
    const res = await fetch(`${BASE}/api/chat/state`);
    const state = await res.json();

    serverDot.className = "dot online";
    serverText.textContent = "Online";

    // Toggle Send / Stop
    if (state.status === "running" || state.status === "thinking") {
      stopBtn.style.display = "flex";
      sendBtn.style.display = "none";
    } else {
      stopBtn.style.display = "none";
      sendBtn.style.display = "flex";
    }

    // Render messages
    if (state.messages && state.messages.length > 0) {
      if (welcomeCard) welcomeCard.style.display = "none";
      renderMessages(state.messages);
    }
  } catch (_) {
    serverDot.className = "dot";
    serverText.textContent = "Offline";
  }
}

let lastRendered = "";
function renderMessages(messages) {
  // Redraw only when the conversation actually changed, and only follow new output if the user is already at the
  // bottom; otherwise scrolling up to read gets yanked back down every poll.
  const snapshot = JSON.stringify(messages);
  if (snapshot === lastRendered) return;
  lastRendered = snapshot;
  const atBottom = chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight < 40;
  const openDrawers = new Set(
    [...chatContainer.querySelectorAll("details.steps-drawer")].map((d, i) => (d.open ? i : -1)).filter((i) => i >= 0)
  );
  chatContainer.innerHTML = messages.map((m) => {
    const isUser = m.role === "user";
    let stepsHtml = "";
    if (m.steps && m.steps.length > 0) {
      stepsHtml = `
        <details class="steps-drawer">
          <summary>Execution Steps (${m.steps.length})</summary>
          <div class="steps-list">
            ${m.steps.map(s => `
              <div class="step-item">
                <span class="gear-pill g${s.gear}">G${s.gear}</span>
                <span>${escapeHtml(s.action || s.kind)}</span>
              </div>
            `).join("")}
          </div>
        </details>
      `;
    }

    let approvalHtml = "";
    if (m.is_approval) {
      approvalHtml = `
        <div class="approval-card">
          <button class="btn-small allow" data-answer="yes">Allow (Yes)</button>
          <button class="btn-small deny" data-answer="no">Deny (No)</button>
        </div>
      `;
    }

    return `
      <div class="msg-row ${isUser ? 'user' : 'assistant'}">
        <div class="msg-bubble">
          ${stepsHtml}
          <div>${formatMarkdown(m.content || "")}</div>
          ${approvalHtml}
        </div>
        <div class="msg-time">${m.time || ""}</div>
      </div>
    `;
  }).join("");

  // Keep drawers the user expanded open; new drawers start collapsed.
  chatContainer.querySelectorAll("details.steps-drawer").forEach((d, i) => {
    if (openDrawers.has(i)) d.open = true;
  });
  if (atBottom) chatContainer.scrollTop = chatContainer.scrollHeight;
}

// User response handler for approvals or prompt inputs
// One delegated listener: Manifest V3 blocks inline onclick handlers, and the buttons are re-created on redraw.
chatContainer.addEventListener("click", (e) => {
  const button = e.target.closest("button[data-answer]");
  if (button) respondInput(button.dataset.answer);
});

window.respondInput = async function(text) {
  try {
    await fetch(`${BASE}/api/chat/respond`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ response: text })
    });
  } catch (err) {
    alert("Error responding: " + err);
  }
};

// Simple clean Markdown renderer
function formatMarkdown(text) {
  if (!text) return "";
  let html = escapeHtml(text);
  // Bold
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  // Links
  html = html.replace(/\[(.*?)\]\((https?:\/\/.*?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  // Lists
  html = html.replace(/^[*-]\s+(.*)$/gm, "<li>$1</li>");
  html = html.replace(/(<li>.*<\/li>)/s, "<ul>$1</ul>");
  // Line breaks
  html = html.replace(/\n\n/g, "</p><p>");
  html = html.replace(/\n/g, "<br>");
  return `<p>${html}</p>`;
}

function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

setInterval(pollChat, 500);
pollChat();
