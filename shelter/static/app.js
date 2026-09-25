// 避難所AI 共通 JS（ビルド無し・外部ライブラリ無し）

// POST して text/event-stream を読む。handlers: {delta(s), queue(n), phase(p), done(obj), error(msg)}
async function streamPost(url, body, handlers) {
  const h = Object.assign({ delta() {}, queue() {}, phase() {}, done() {}, error() {} }, handlers);
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    h.error("接続できません / Cannot connect");
    return;
  }
  const ctype = res.headers.get("content-type") || "";
  if (!res.ok || !ctype.includes("text/event-stream")) {
    let msg = "HTTP " + res.status;
    try { const j = await res.json(); if (j.error) msg = j.error.message; } catch (e) {}
    h.error(msg);
    return;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch (e) { continue; }
        if (ev.delta !== undefined) h.delta(ev.delta);
        else if (ev.queue !== undefined) h.queue(ev.queue);
        else if (ev.phase !== undefined) h.phase(ev.phase);
        else if (ev.error) h.error(ev.error);
        else if (ev.done) h.done(ev);
      }
    }
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// AI の回答の最小限の Markdown（太字・見出し記号）を表示用に整える。先にエスケープするので安全
function renderAnswer(s) {
  return escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/^#{1,4}\s+(.*)$/gm, "<strong>$1</strong>")
    .replace(/^(\s*)[*-]\s+/gm, "$1・");
}

// 端末ごとの匿名セッション ID（氏名等とは結びつかない乱数）
function sessionId() {
  const k = "shelter_session";
  let v = null;
  try { v = localStorage.getItem(k); } catch (e) {}
  if (!v) {
    const a = new Uint8Array(8);
    (window.crypto || window.msCrypto).getRandomValues(a);
    v = Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
    try { localStorage.setItem(k, v); } catch (e) {}
  }
  return v;
}

// AI の切り替え（この PC の AI ⇄ クラウド AI）。状態は端末の localStorage に持つ
const AI_BACKEND_KEY = "shelter_ai_backend";
function savedAiBackend() {
  try { return localStorage.getItem(AI_BACKEND_KEY) === "gemini" ? "gemini" : "local"; } catch (e) { return "local"; }
}
// スイッチ（input[type=checkbox]）を結びつけ、今のモードを返す関数を返す。スイッチが無ければ常に local
// onChange(mode) は最初に1回と、切り替えるたびに呼ばれる。無効（disabled）のスイッチは local 扱い
function bindAiSwitch(input, onChange) {
  const current = () => (input && input.checked && !input.disabled ? "gemini" : "local");
  if (input) {
    input.checked = !input.disabled && savedAiBackend() === "gemini";  // 無効のときは PC 側を見せる
    input.addEventListener("change", () => {
      try { localStorage.setItem(AI_BACKEND_KEY, input.checked ? "gemini" : "local"); } catch (e) {}
      if (onChange) onChange(current());
    });
  }
  if (onChange) onChange(current());
  return current;
}

// 同行者（家族）の入力欄（templates/_members.html）。追加・削除・番号の振り直し・合計人数
function bindMembers(root) {
  if (!root) return;
  const list = root.querySelector(".members-list");
  const tpl = root.querySelector("template");
  const total = root.querySelector("[data-total]");
  const form = root.closest("form");
  const hh = root.dataset.hh && form ? form.querySelector('[name="' + root.dataset.hh + '"]') : null;
  let next = parseInt(root.dataset.next || "0", 10);
  function refresh() {
    const items = list.querySelectorAll("[data-member]");
    items.forEach((el, k) => { el.querySelector("[data-no]").textContent = k + 1; });
    const n = 1 + items.length;
    if (hh && parseInt(hh.value || "1", 10) < n) hh.value = n;
    if (total) total.textContent = (root.dataset.totalFmt || "{n}").replace("{n}", hh ? Math.max(n, parseInt(hh.value || "1", 10)) : n);
  }
  root.querySelector("[data-add]").addEventListener("click", () => {
    const box = document.createElement("div");
    box.innerHTML = tpl.innerHTML.split("__i__").join(String(next++));
    const el = box.firstElementChild;
    list.appendChild(el);
    refresh();
    const first = el.querySelector("input");
    if (first) first.focus();
  });
  list.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-remove]");
    if (!btn) return;
    btn.closest("[data-member]").remove();
    if (hh) hh.value = Math.max(1 + list.querySelectorAll("[data-member]").length, parseInt(hh.value || "1", 10) - 1);
    refresh();
  });
  if (hh) hh.addEventListener("input", refresh);
  refresh();
}
document.querySelectorAll("[data-members]").forEach(bindMembers);
