const STORAGE_KEY = "dtVintedPair";
const STATUS_KEY = "dtVintedStatus";
const MAX_PAIR_AGE_MS = 16 * 60 * 1000;

function b64url(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function mapSameSite(value) {
  const v = String(value || "").toLowerCase();
  if (v === "strict") return "Strict";
  if (v === "lax") return "Lax";
  if (v === "no_restriction") return "None";
  return "";
}

async function setStatus(text) {
  await chrome.storage.local.set({ [STATUS_KEY]: String(text || "") });
}

async function getPair() {
  const data = await chrome.storage.local.get(STORAGE_KEY);
  const pair = data[STORAGE_KEY];
  if (!pair || typeof pair !== "object") return null;
  const age = Date.now() - Number(pair.ts || 0);
  if (!pair.u || !String(pair.u).startsWith("https://") || !pair.t || age < -60_000 || age > MAX_PAIR_AGE_MS) {
    await chrome.storage.local.remove(STORAGE_KEY);
    return null;
  }
  return pair;
}

async function captureAndReturn(userId) {
  const pair = await getPair();
  if (!pair) return;
  if (pair.uploading) return;
  pair.uploading = true;
  await chrome.storage.local.set({ [STORAGE_KEY]: pair });
  try {
    const cookies = await chrome.cookies.getAll({ domain: "vinted.de" });
    const filtered = cookies
      .filter(c => {
        const d = String(c.domain || "").toLowerCase().replace(/^\./, "");
        return d === "vinted.de" || d.endsWith(".vinted.de");
      })
      .map(c => {
        const out = {
          name: String(c.name || ""),
          value: String(c.value || ""),
          domain: String(c.domain || ".vinted.de"),
          path: String(c.path || "/"),
          httpOnly: Boolean(c.httpOnly),
          secure: Boolean(c.secure)
        };
        if (Number(c.expirationDate || 0) > 0) out.expires = Number(c.expirationDate);
        const ss = mapSameSite(c.sameSite);
        if (ss) out.sameSite = ss;
        return out;
      });
    if (!filtered.some(c => c.name === "access_token_web")) {
      pair.uploading = false;
      await chrome.storage.local.set({ [STORAGE_KEY]: pair });
      await setStatus("Vinted подтвердил аккаунт, но access_token_web ещё не появился. Подожди пару секунд.");
      return;
    }
    const session = {
      cookies: filtered,
      origins: [],
      user_agent: navigator.userAgent || "",
      locale: navigator.language || "de-DE",
      captured_by: "dt-vinted-local-helper",
      authenticated_user_id: String(userId || ""),
      metric_endpoint_template: "/api/v2/items/{item_id}/details?localize=true"
    };
    const envelope = { token: pair.t, session };
    const url = String(pair.u).replace(/\/$/, "") + "/local/receive#payload=" + b64url(JSON.stringify(envelope));
    await chrome.storage.local.remove(STORAGE_KEY);
    await setStatus("Сессия подтверждена. Открываю DT Session Worker…");
    await chrome.tabs.create({ url });
  } catch (error) {
    pair.uploading = false;
    await chrome.storage.local.set({ [STORAGE_KEY]: pair });
    await setStatus("Ошибка Local Helper: " + String(error || "unknown"));
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (!message || typeof message !== "object") return;
    if (message.type === "DT_VINTED_PAIR") {
      const pair = message.pair || {};
      if (!String(pair.u || "").startsWith("https://") || !String(pair.t || "")) return;
      await chrome.storage.local.set({ [STORAGE_KEY]: { u: String(pair.u), t: String(pair.t), ts: Number(pair.ts || Date.now()), uploading: false } });
      await setStatus("Связь с DT Session установлена. Войди в Vinted — сохранение произойдёт автоматически.");
      return;
    }
    if (message.type === "DT_VINTED_AUTHENTICATED") {
      await captureAndReturn(String(message.userId || ""));
    }
  })().then(() => sendResponse({ ok: true })).catch(() => sendResponse({ ok: false }));
  return true;
});

chrome.runtime.onInstalled.addListener(() => {
  setStatus("Local Helper установлен. Открой одноразовую ссылку из DT Vinted Session.");
});
