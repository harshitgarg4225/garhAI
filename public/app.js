"use strict";

const chatEl = document.getElementById("chat");
const emptyState = document.getElementById("empty-state");
const composer = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const sendLabel = sendBtn.querySelector(".send-label");
const newChatBtn = document.getElementById("new-chat");
const statusPill = document.getElementById("status-pill");
const configBanner = document.getElementById("config-banner");

/** @type {{role: "user"|"assistant", content: string}[]} */
let history = [];
let streaming = false;
let abortController = null;

// ---------- Markdown rendering (escape-first, no external deps) ----------

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderInline(text) {
  let out = escapeHtml(text);
  // Inline code first so other rules don't fire inside it.
  const codeSlots = [];
  out = out.replace(/`([^`\n]+)`/g, (_m, code) => {
    codeSlots.push(`<code>${code}</code>`);
    return `\u0000${codeSlots.length - 1}\u0000`;
  });
  out = out
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(
      /\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
  out = out.replace(/\u0000(\d+)\u0000/g, (_m, i) => codeSlots[Number(i)]);
  return out;
}

function renderMarkdown(text) {
  // Split out fenced code blocks first; everything else goes through
  // the line-based renderer.
  const parts = text.split(/```([\w+-]*)\n?([\s\S]*?)(?:```|$)/g);
  let html = "";
  for (let i = 0; i < parts.length; i += 3) {
    html += renderBlocks(parts[i]);
    if (i + 2 < parts.length) {
      const code = parts[i + 2].replace(/\n$/, "");
      html += `<pre><code>${escapeHtml(code)}</code></pre>`;
    }
  }
  return html;
}

function renderBlocks(text) {
  const lines = text.split("\n");
  let html = "";
  let list = null; // "ul" | "ol" | null
  let paragraph = [];

  const closeList = () => {
    if (list) {
      html += `</${list}>`;
      list = null;
    }
  };
  const flushParagraph = () => {
    if (paragraph.length) {
      html += `<p>${paragraph.map(renderInline).join("<br>")}</p>`;
      paragraph = [];
    }
  };

  for (const line of lines) {
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const ordered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const quote = /^>\s?(.*)$/.exec(line);

    if (line.trim() === "") {
      flushParagraph();
      closeList();
    } else if (heading) {
      flushParagraph();
      closeList();
      const level = heading[1].length;
      html += `<h${level}>${renderInline(heading[2])}</h${level}>`;
    } else if (/^(-{3,}|\*{3,})$/.test(line.trim())) {
      flushParagraph();
      closeList();
      html += "<hr>";
    } else if (bullet) {
      flushParagraph();
      if (list !== "ul") {
        closeList();
        html += "<ul>";
        list = "ul";
      }
      html += `<li>${renderInline(bullet[1])}</li>`;
    } else if (ordered) {
      flushParagraph();
      if (list !== "ol") {
        closeList();
        html += "<ol>";
        list = "ol";
      }
      html += `<li>${renderInline(ordered[1])}</li>`;
    } else if (quote) {
      flushParagraph();
      closeList();
      html += `<blockquote>${renderInline(quote[1])}</blockquote>`;
    } else {
      closeList();
      paragraph.push(line);
    }
  }
  flushParagraph();
  closeList();
  return html;
}

// ---------- UI helpers ----------

function hideEmptyState() {
  if (emptyState) emptyState.classList.add("hidden");
}

function scrollToBottom() {
  chatEl.scrollTop = chatEl.scrollHeight;
}

function isNearBottom() {
  return chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight < 120;
}

function addUserMessage(text) {
  hideEmptyState();
  const wrap = document.createElement("div");
  wrap.className = "msg msg-user";
  const body = document.createElement("div");
  body.className = "msg-body";
  body.textContent = text;
  wrap.appendChild(body);
  chatEl.appendChild(wrap);
  scrollToBottom();
}

function addAssistantShell() {
  hideEmptyState();
  const wrap = document.createElement("div");
  wrap.className = "msg msg-assistant";

  const thinking = document.createElement("details");
  thinking.className = "thinking hidden";
  const summary = document.createElement("summary");
  summary.textContent = "Thinking…";
  const thinkingBody = document.createElement("div");
  thinkingBody.className = "thinking-body";
  thinking.appendChild(summary);
  thinking.appendChild(thinkingBody);

  const body = document.createElement("div");
  body.className = "msg-body md";
  body.innerHTML = '<span class="cursor"></span>';

  wrap.appendChild(thinking);
  wrap.appendChild(body);
  chatEl.appendChild(wrap);
  scrollToBottom();
  return { wrap, body, thinking, thinkingBody, summary };
}

function addSystemMessage(kind, text) {
  hideEmptyState();
  const wrap = document.createElement("div");
  wrap.className = `msg msg-${kind}`;
  const body = document.createElement("div");
  body.className = "msg-body";
  body.textContent = text;
  wrap.appendChild(body);
  chatEl.appendChild(wrap);
  scrollToBottom();
}

function setStreaming(on) {
  streaming = on;
  input.disabled = on;
  if (on) {
    sendLabel.textContent = "Stop";
    sendBtn.classList.remove("btn-primary");
    sendBtn.classList.add("btn-danger");
    sendBtn.disabled = false;
  } else {
    sendLabel.textContent = "Send";
    sendBtn.classList.add("btn-primary");
    sendBtn.classList.remove("btn-danger");
    sendBtn.disabled = input.value.trim() === "";
    input.focus();
  }
}

// ---------- Chat flow ----------

async function sendMessage(text) {
  history.push({ role: "user", content: text });
  addUserMessage(text);

  const shell = addAssistantShell();
  let answer = "";
  let thinkingText = "";
  let renderQueued = false;

  const renderAnswer = (done) => {
    if (renderQueued && !done) return;
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;
      const keepPinned = isNearBottom();
      shell.body.innerHTML =
        renderMarkdown(answer) + (done ? "" : '<span class="cursor"></span>');
      if (keepPinned) scrollToBottom();
    });
  };

  setStreaming(true);
  abortController = new AbortController();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
      signal: abortController.signal,
    });

    if (!res.ok) {
      let message = `Request failed (${res.status}).`;
      try {
        const data = await res.json();
        if (data.error) message = data.error;
      } catch {
        // Non-JSON error body; keep the generic message.
      }
      shell.wrap.remove();
      addSystemMessage("error", message);
      history.pop();
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const rawEvent of events) {
        const dataLine = rawEvent
          .split("\n")
          .find((l) => l.startsWith("data: "));
        if (!dataLine) continue;
        let payload;
        try {
          payload = JSON.parse(dataLine.slice(6));
        } catch {
          continue;
        }

        if (payload.type === "text") {
          answer += payload.text;
          renderAnswer(false);
        } else if (payload.type === "thinking") {
          thinkingText += payload.text;
          shell.thinking.classList.remove("hidden");
          shell.thinkingBody.textContent = thinkingText;
        } else if (payload.type === "notice") {
          addSystemMessage("notice", payload.text);
        } else if (payload.type === "error") {
          addSystemMessage("error", payload.message);
        } else if (payload.type === "done") {
          shell.summary.textContent = "Thought process";
        }
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") {
      addSystemMessage("error", "Connection lost while streaming. Try again.");
    }
  } finally {
    renderAnswer(true);
    if (answer.trim() === "") {
      shell.body.innerHTML = "<em>(no response)</em>";
      history.push({ role: "assistant", content: "(no response)" });
    } else {
      history.push({ role: "assistant", content: answer });
    }
    abortController = null;
    setStreaming(false);
  }
}

// ---------- Events ----------

composer.addEventListener("submit", (e) => {
  e.preventDefault();
  if (streaming) {
    abortController?.abort();
    return;
  }
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  autoGrow();
  sendMessage(text);
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    composer.requestSubmit();
  }
});

input.addEventListener("input", () => {
  sendBtn.disabled = streaming ? false : input.value.trim() === "";
  autoGrow();
});

function autoGrow() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
}

newChatBtn.addEventListener("click", () => {
  if (streaming) abortController?.abort();
  history = [];
  chatEl.querySelectorAll(".msg").forEach((el) => el.remove());
  emptyState.classList.remove("hidden");
  input.focus();
});

document.querySelectorAll(".suggestion").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (streaming) return;
    input.value = btn.textContent.trim();
    sendBtn.disabled = false;
    input.focus();
    autoGrow();
  });
});

// ---------- Health check ----------

(async () => {
  try {
    const res = await fetch("/health");
    const data = await res.json();
    if (data.apiKeyConfigured) {
      statusPill.textContent = "online";
      statusPill.classList.remove("pill-muted");
      statusPill.classList.add("pill-ok");
    } else {
      statusPill.textContent = "needs API key";
      statusPill.classList.remove("pill-muted");
      statusPill.classList.add("pill-warn");
      configBanner.classList.remove("hidden");
    }
  } catch {
    statusPill.textContent = "offline";
  }
})();
