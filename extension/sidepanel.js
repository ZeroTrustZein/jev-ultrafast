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
function updateActiveTabContext() {
  if (typeof chrome !== "undefined" && chrome.tabs) {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs && tabs[0]) {
        currentTabId = tabs[0].id;
        const validUrl = tabs[0].url && !/^(brave|chrome|edge|about):/.test(tabs[0].url) && !tabs[0].url.includes("chromewebstore") ? tabs[0].url : "";
        if (validUrl) {
          currentTabUrl = validUrl;
          if (!agentTabId || (lockTabCb && lockTabCb.checked)) {
            contextUrlEl.textContent = currentTabUrl;
          }
        }
      }
    });
  }
}
updateActiveTabContext();

if (typeof chrome !== "undefined" && chrome.tabs) {
  if (chrome.tabs.onActivated) {
    chrome.tabs.onActivated.addListener(() => updateActiveTabContext());
  }
  if (chrome.tabs.onUpdated) {
    chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
      if (tab.active) updateActiveTabContext();
    });
  }
  if (chrome.tabs.onRemoved) {
    chrome.tabs.onRemoved.addListener((id) => {
      if (id === agentTabId) {
        agentTabId = null;
        contextUrlEl.textContent = "Agent tab closed";
        updateActiveTabContext();
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
      let tab = await chrome.tabs.get(agentTabId);
      if (tab) {
        if ((!tab.url || tab.url === "about:blank") && preferredUrl && preferredUrl !== "about:blank") {
          tab = await chrome.tabs.update(agentTabId, { url: preferredUrl });
          await new Promise((resolve) => setTimeout(resolve, 300));
        }
        return tab;
      }
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
          let tab = tabs[0];
          if ((!tab.url || tab.url === "about:blank") && preferredUrl && preferredUrl !== "about:blank") {
            tab = await chrome.tabs.update(agentTabId, { url: preferredUrl });
            await new Promise((resolve) => setTimeout(resolve, 300));
          }
          return tab;
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

// 2. Auto-grow composer textarea & paste handling
function resizeComposer() {
  composerInput.style.height = "auto";
  const nextHeight = Math.min(Math.max(composerInput.scrollHeight, 22), 120);
  composerInput.style.height = nextHeight + "px";
  if (composerInput.value.trim()) {
    sendBtn.classList.add("active");
  } else {
    sendBtn.classList.remove("active");
  }
}

composerInput.addEventListener("input", resizeComposer);
composerInput.addEventListener("paste", () => {
  setTimeout(resizeComposer, 0);
});

// 3. Quick Chips
document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    composerInput.value = chip.getAttribute("data-prompt");
    resizeComposer();
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

  const url = isLocked && currentTabUrl ? currentTabUrl : (currentTabUrl || seedUrl || null);
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

let currentSessionId = sessionStorage.getItem("jev_session_id") || null;
let pollSeq = 0;

// Reset to clean session
async function startFreshSession() {
  pollSeq++;
  clearTimeout(pollTimeout);
  try {
    const res = await fetch(`${BASE}/api/chat/history/new`, { method: "POST" });
    const state = await res.json();
    currentSessionId = state.id;
    sessionStorage.setItem("jev_session_id", currentSessionId);
  } catch (_) {
    currentSessionId = `sess_${Date.now()}`;
    sessionStorage.setItem("jev_session_id", currentSessionId);
  }
  renderEmptyChat();
  pollChat();
}

resetBtn.addEventListener("click", startFreshSession);

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
          <div class="history-item ${s.id === currentSessionId ? 'active' : ''}" data-id="${s.id}">
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
          pollSeq++;
          clearTimeout(pollTimeout);
          try {
            const res = await fetch(`${BASE}/api/chat/history/load`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ id })
            });
            const state = await res.json();
            currentSessionId = state.id;
            sessionStorage.setItem("jev_session_id", currentSessionId);
            lastRendered = "";
            toggleHistory(false);
            if (state.messages && state.messages.length > 0) {
              renderMessages(state.messages, state.status);
            } else {
              renderEmptyChat();
            }
          } catch (_) {}
          pollChat();
        });
      });
    } catch (_) {}
  }
}

if (historyBtn) historyBtn.addEventListener("click", () => toggleHistory());
if (historyCloseBtn) historyCloseBtn.addEventListener("click", () => toggleHistory(false));
if (newChatBtn) newChatBtn.addEventListener("click", () => {
  toggleHistory(false);
  startFreshSession();
});

// 7. Poll State
let pollTimeout = null;
async function pollChat() {
  clearTimeout(pollTimeout);
  const thisSeq = ++pollSeq;
  let nextDelay = 1000;
  try {
    const res = await fetch(`${BASE}/api/chat/state`);
    const state = await res.json();

    if (thisSeq !== pollSeq) return;

    serverDot.className = "dot online";
    serverText.textContent = "Online";

    // Toggle Send / Stop & increase poll frequency while running
    if (state.status === "running" || state.status === "thinking") {
      stopBtn.style.display = "flex";
      sendBtn.style.display = "none";
      nextDelay = 200; // 200ms ultra-fast live streaming during execution
    } else {
      stopBtn.style.display = "none";
      sendBtn.style.display = "flex";
      nextDelay = 1000;
    }

    // Keep session ID synced if backend session matches or if this is the active agent
    if (!currentSessionId) {
      if (state.status === "running" || state.status === "thinking" || state.status === "waiting_user") {
        currentSessionId = state.id;
        sessionStorage.setItem("jev_session_id", currentSessionId);
      } else {
        // First open with idle server: start fresh
        return startFreshSession();
      }
    }

    if (state.id !== currentSessionId) {
      // Backend transitioned or loaded another session
      currentSessionId = state.id;
      sessionStorage.setItem("jev_session_id", currentSessionId);
    }

    // Render messages
    if (state.messages && state.messages.length > 0) {
      renderMessages(state.messages, state.status);
    } else {
      renderEmptyChat();
    }
  } catch (_) {
    serverDot.className = "dot";
    serverText.textContent = "Offline";
    nextDelay = 2000;
  }
  pollTimeout = setTimeout(pollChat, nextDelay);
}

function renderEmptyChat() {
  if (lastRendered === "EMPTY") return;
  lastRendered = "EMPTY";
  chatContainer.innerHTML = `
    <div class="welcome-card" id="welcome-card">
      <div class="hero-badge">
        <span class="hero-pill">420M Multimodal</span>
        <span class="hero-sub">RTX 4070 Ti Local</span>
      </div>
      <div class="welcome-title">Autonomous Visual Browser Co-Pilot</div>
      <div class="welcome-desc">Executes complex web actions via dual-gear policy: System-1 DOM actions in 75ms with instant System-2 visual fallback in 99ms. Zero cloud cost.</div>
      <div class="chips-row">
        <button class="chip" data-prompt="Find and open the Wikipedia article about Alan Turing, then click on the Early computing section.">
          <span class="chip-title">Wikipedia Research</span>
          <span class="chip-desc">Navigate & read technical articles</span>
        </button>
        <button class="chip" data-prompt="Go to news.ycombinator.com, find the first story about AI or machine learning, and open its comments thread.">
          <span class="chip-title">Hacker News Thread</span>
          <span class="chip-desc">Find story & inspect discussion</span>
        </button>
        <button class="chip" data-prompt="i am a student in leipzig i wanna renew my residence permit please go to the form fill it for me stop and ask me if u need anything">
          <span class="chip-title">Document & Permit Form</span>
          <span class="chip-desc">Fill applications & request details</span>
        </button>
      </div>
    </div>
  `;
}

let lastRendered = "";
function renderMessages(messages, currentStatus) {
  // Redraw only when the conversation actually changed
  const isAgentRunning = (currentStatus === "running");
  const snapshot = JSON.stringify({ messages, currentStatus });
  if (snapshot === lastRendered) return;
  lastRendered = snapshot;
  const atBottom = chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight < 50;
  const openDrawers = new Set(
    [...chatContainer.querySelectorAll("details.steps-drawer")].map((d, i) => (d.open ? i : -1)).filter((i) => i >= 0)
  );
  chatContainer.innerHTML = messages.map((m, mIdx) => {
    const isUser = m.role === "user";
    const isLastAsst = (!isUser && mIdx === messages.length - 1);
    const shouldAutoOpen = (isLastAsst && isAgentRunning);

    let stepsHtml = "";
    if (m.steps && m.steps.length > 0) {
      stepsHtml = `
        <details class="steps-drawer" ${shouldAutoOpen ? "open" : ""}>
          <summary>Execution Steps (${m.steps.length}) ${shouldAutoOpen ? '⚡ Live' : ''}</summary>
          <div class="steps-list">
            ${m.steps.map((s, sIdx) => {
              const isLatest = (sIdx === m.steps.length - 1 && isLastAsst && isAgentRunning);
              return `
                <div class="step-item ${isLatest ? 'live-latest' : ''}">
                  ${isLatest ? '<span class="step-pulse"></span>' : ''}
                  <span class="gear-pill g${s.gear}">G${s.gear}</span>
                  <span>${escapeHtml(s.action || s.kind)}</span>
                </div>
              `;
            }).join("")}
          </div>
        </details>
      `;
    }

    let liveTickerHtml = "";
    if (isLastAsst && isAgentRunning) {
      const lastStep = m.steps && m.steps.length > 0 ? (m.steps[m.steps.length - 1].action || m.steps[m.steps.length - 1].kind) : "Starting agent...";
      liveTickerHtml = `
        <div class="live-action-ticker">
          <span class="step-pulse"></span>
          <span>⚡ Running: ${escapeHtml(lastStep)}</span>
        </div>
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

    let copyFooterHtml = "";
    if (!isUser) {
      copyFooterHtml = `
        <div class="msg-bubble-footer">
          <button class="msg-copy-btn" title="Copy message text">📋 Copy</button>
        </div>
      `;
    }

    return `
      <div class="msg-row ${isUser ? 'user' : 'assistant'}">
        <div class="msg-bubble">
          ${stepsHtml}
          ${liveTickerHtml}
          <div class="msg-content">${formatMarkdown(m.content || "")}</div>
          ${approvalHtml}
          ${copyFooterHtml}
        </div>
        <div class="msg-time">${m.time || ""}</div>
      </div>
    `;
  }).join("");

  // Keep drawers the user expanded open
  chatContainer.querySelectorAll("details.steps-drawer").forEach((d, i) => {
    if (openDrawers.has(i)) d.open = true;
  });
  if (atBottom || isAgentRunning) chatContainer.scrollTop = chatContainer.scrollHeight;
}

// User response handler, quick chips, and copy buttons
// Delegated on chatContainer so all dynamically created buttons work seamlessly
chatContainer.addEventListener("click", async (e) => {
  // Quick chip click
  const chip = e.target.closest(".chip");
  if (chip) {
    composerInput.value = chip.getAttribute("data-prompt") || "";
    resizeComposer();
    sendMessage();
    return;
  }

  // Code snippet copy button
  const codeCopy = e.target.closest(".code-copy-btn");
  if (codeCopy) {
    const block = codeCopy.closest(".code-block");
    const codeEl = block ? block.querySelector("pre code") : null;
    const text = codeEl ? codeEl.textContent : "";
    if (text) {
      const ok = await copyTextToClipboard(text);
      if (ok) {
        codeCopy.classList.add("copied");
        codeCopy.textContent = "✓ Copied";
        setTimeout(() => {
          codeCopy.classList.remove("copied");
          codeCopy.textContent = "Copy";
        }, 1500);
      }
    }
    return;
  }

  // Assistant message bubble copy button
  const msgCopy = e.target.closest(".msg-copy-btn");
  if (msgCopy) {
    const bubble = msgCopy.closest(".msg-bubble");
    const contentEl = bubble ? bubble.querySelector(".msg-content") : null;
    const text = contentEl ? contentEl.innerText : (bubble ? bubble.innerText : "");
    if (text) {
      const ok = await copyTextToClipboard(text);
      if (ok) {
        msgCopy.classList.add("copied");
        msgCopy.textContent = "✓ Copied";
        setTimeout(() => {
          msgCopy.classList.remove("copied");
          msgCopy.textContent = "📋 Copy";
        }, 1500);
      }
    }
    return;
  }

  // Approval buttons
  const button = e.target.closest("button[data-answer]");
  if (button) respondInput(button.dataset.answer);
});

// Clipboard copy helper with fallback
async function copyTextToClipboard(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) {}
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    ta.style.left = "-9999px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (_) {
    return false;
  }
}

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

// Clean Markdown renderer supporting code blocks and inline code
function formatMarkdown(text) {
  if (!text) return "";
  let html = escapeHtml(text);

  // Triple backtick code blocks with language and copy button
  html = html.replace(/```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g, (_match, lang, code) => {
    const cleanLang = (lang || "code").trim();
    return `
      <div class="code-block">
        <div class="code-block-header">
          <span class="code-lang">${cleanLang}</span>
          <button class="code-copy-btn" title="Copy code snippet">Copy</button>
        </div>
        <pre><code>${code.replace(/^\n+|\n+$/g, '')}</code></pre>
      </div>
    `;
  });

  // Inline backtick code `foo`
  html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");

  // Bold
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");

  // Links
  html = html.replace(/\[(.*?)\]\((https?:\/\/.*?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  // Lists
  html = html.replace(/^[*-]\s+(.*)$/gm, "<li>$1</li>");
  html = html.replace(/(<li>.*<\/li>)/s, "<ul>$1</ul>");

  // Paragraphs and breaks
  html = html.replace(/\n\n/g, "</p><p>");
  html = html.replace(/\n/g, "<br>");
  return `<p>${html}</p>`;
}

function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

pollChat();
