const BASE = "http://127.0.0.1:8766";
let currentTabUrl = "";
let currentTabId = null;
let agentTabId = null;
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
const historyBtn = document.getElementById("history-btn");
const historyOverlay = document.getElementById("history-overlay");
const historyCloseBtn = document.getElementById("history-close-btn");
const historyList = document.getElementById("history-list");
const newChatBtn = document.getElementById("new-chat-btn");
const filePicker = document.getElementById("file-picker");
const attachmentsList = document.getElementById("attachments-list");
const serverDot = document.getElementById("server-dot");
const serverText = document.getElementById("server-text");

// Click context bar to view/focus agent tab
contextUrlEl.style.cursor = "pointer";
contextUrlEl.title = "Click to focus agent tab";
contextUrlEl.addEventListener("click", () => {
  if (agentTabId) {
    chrome.tabs.update(agentTabId, { active: true }).catch(() => {});
  }
});

// 1. Detect current browser tab
if (typeof chrome !== "undefined" && chrome.tabs) {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (tabs && tabs[0]) {
      currentTabId = tabs[0].id;
      if (tabs[0].url) {
        currentTabUrl = tabs[0].url;
        if (!agentTabId) {
          contextUrlEl.textContent = currentTabUrl;
        }
      }
    }
  });

  if (chrome.tabs.onRemoved) {
    chrome.tabs.onRemoved.addListener((id) => {
      if (id === agentTabId) {
        agentTabId = null;
        contextUrlEl.textContent = "Agent tab closed";
      }
    });
  }
}

// Dedicated Agent Tab & Tab Group helper
async function getOrCreateAgentTab(preferredUrl) {
  if (lockTabCb && lockTabCb.checked) {
    let [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) {
      if (!tab.url || /^(brave|chrome|edge|about):/.test(tab.url) || tab.url.includes("chromewebstore")) {
        tab = await chrome.tabs.update(tab.id, { url: "about:blank" });
        await new Promise((resolve) => setTimeout(resolve, 300));
      }
      agentTabId = tab.id;
      return tab;
    }
  }

  if (agentTabId !== null) {
    try {
      const tab = await chrome.tabs.get(agentTabId);
      if (tab) return tab;
    } catch (_) {
      agentTabId = null;
    }
  }

  if (chrome.tabGroups && chrome.tabGroups.query) {
    try {
      const groups = await chrome.tabGroups.query({ title: "Jev Agent" });
      for (const group of groups) {
        const tabs = await chrome.tabs.query({ groupId: group.id });
        if (tabs && tabs.length > 0) {
          agentTabId = tabs[0].id;
          return tabs[0];
        }
      }
    } catch (e) {
      console.warn("Could not query tab groups:", e);
    }
  }

  const startUrl = preferredUrl && preferredUrl !== "about:blank" ? preferredUrl : "about:blank";
  const newTab = await chrome.tabs.create({ url: startUrl, active: false });
  agentTabId = newTab.id;

  if (chrome.tabs.group) {
    try {
      const groupId = await chrome.tabs.group({ tabIds: [newTab.id] });
      if (chrome.tabGroups && chrome.tabGroups.update) {
        await chrome.tabGroups.update(groupId, {
          title: "Jev Agent",
          color: "purple",
          collapsed: false,
        });
      }
    } catch (err) {
      console.warn("Could not group agent tab:", err);
    }
  }

  return newTab;
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
// The browser never exposes a picked file's disk path, so send the bytes to the local agent, which stores them.
filePicker.addEventListener("change", async (e) => {
  for (const f of e.target.files) {
    if (attachedFiles.includes(f.name)) continue;
    attachedFiles.push(f.name + " (uploading…)");
    renderAttachments();
    try {
      const res = await fetch(`${BASE}/api/chat/upload`, {
        method: "POST",
        headers: { "X-File-Name": encodeURIComponent(f.name) },
        body: f,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.status);
      attachedFiles[attachedFiles.indexOf(f.name + " (uploading…)")] = f.name;
    } catch (err) {
      attachedFiles.splice(attachedFiles.indexOf(f.name + " (uploading…)"), 1);
      alert(`Could not attach ${f.name}: ${err.message || err}`);
    }
    renderAttachments();
  }
  filePicker.value = "";
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

  // Determine starting URL context (from user's active tab if on a regular page)
  let [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  let seedUrl = "";
  if (activeTab && activeTab.url && !/^(brave|chrome|edge|about):/.test(activeTab.url) && !activeTab.url.includes("chromewebstore")) {
    seedUrl = activeTab.url;
  }

  const tab = await getOrCreateAgentTab(seedUrl);
  if (!tab) return;

  currentTabId = tab.id;
  currentTabUrl = tab.url && tab.url !== "about:blank" ? tab.url : "";
  const isLocked = lockTabCb && lockTabCb.checked;
  contextUrlEl.textContent = isLocked ? currentTabUrl : `📁 [Jev Agent] ${currentTabUrl || "about:blank"}`;

  composerInput.value = "";
  composerInput.style.height = "auto";
  sendBtn.classList.remove("active");
  if (welcomeCard) welcomeCard.style.display = "none";

  // Attach to agent tab before starting
  const attached = await chrome.runtime.sendMessage({ type: "attach", tabId: currentTabId });
  if (!attached || !attached.ok) {
    alert("Could not take control of agent tab: " + ((attached && attached.error) || "unknown error"));
    return;
  }

  const url = isLocked && currentTabUrl ? currentTabUrl : (currentTabUrl || null);
  const files = attachedFiles.filter((name) => !name.endsWith("(uploading…)"));

  try {
    const res = await fetch(`${BASE}/api/chat/send`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, files, url })
    });
    const data = await res.json();
    if (!res.ok) alert(data.error || "Jev could not start this task");
    attachedFiles = [];
    renderAttachments();
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
    await fetch(`${BASE}/api/chat/history/new`, { method: "POST" });
    chatContainer.innerHTML = "";
    if (welcomeCard) chatContainer.appendChild(welcomeCard);
    welcomeCard.style.display = "block";
    lastMessageCount = 0;
    lastRendered = "";
    pollChat();
  } catch (_) {}
});

// History Panel Toggle & Selection
async function toggleHistory(show) {
  if (show === undefined) show = historyOverlay.style.display === "none";
  historyOverlay.style.display = show ? "flex" : "none";
  if (show) {
    try {
      const res = await fetch(`${BASE}/api/chat/history`);
      const list = await res.json();
      historyList.innerHTML = list.length === 0
        ? '<div style="color:#94a3b8;text-align:center;margin-top:24px;">No past sessions yet</div>'
        : list.map(s => `
          <div class="history-item ${s.active ? 'active' : ''}" data-id="${s.id}">
            <div class="history-item-title">${escapeHtml(s.title || 'Untitled Session')}</div>
            <div class="history-item-meta">
              <span>${s.message_count} messages</span>
              <span>${s.updated_at || ''}</span>
            </div>
          </div>
        `).join("");

      document.querySelectorAll(".history-item").forEach(el => {
        el.addEventListener("click", async () => {
          const id = el.getAttribute("data-id");
          await fetch(`${BASE}/api/chat/history/load`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id })
          });
          toggleHistory(false);
          lastRendered = "";
          pollChat();
        });
      });
    } catch (_) {}
  }
}

if (historyBtn) historyBtn.addEventListener("click", () => toggleHistory());
if (historyCloseBtn) historyCloseBtn.addEventListener("click", () => toggleHistory(false));
if (newChatBtn) newChatBtn.addEventListener("click", async () => {
  await fetch(`${BASE}/api/chat/history/new`, { method: "POST" });
  toggleHistory(false);
  lastRendered = "";
  chatContainer.innerHTML = "";
  if (welcomeCard) chatContainer.appendChild(welcomeCard);
  welcomeCard.style.display = "block";
  pollChat();
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
