(() => {
  const PAIR_KEY = "dtv";

  function decodeB64Url(value) {
    let s = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
    while (s.length % 4) s += "=";
    return decodeURIComponent(escape(atob(s)));
  }

  function readPairFromFragment() {
    const params = new URLSearchParams(location.hash.slice(1));
    const encoded = params.get(PAIR_KEY);
    if (!encoded) return null;
    try {
      const pair = JSON.parse(decodeB64Url(encoded));
      if (!pair || typeof pair !== "object") return null;
      const u = String(pair.u || "");
      const t = String(pair.t || "");
      if (!u.startsWith("https://") || !t || t.length > 80) return null;
      return { u, t, ts: Number(pair.ts || Date.now()) };
    } catch (_) {
      return null;
    }
  }

  async function confirmCurrentUser() {
    try {
      const response = await fetch("/api/v2/users/current", {
        method: "GET",
        credentials: "include",
        headers: { "Accept": "application/json, text/plain, */*" },
        cache: "no-store"
      });
      if (response.status !== 200) return "";
      let data = null;
      try { data = await response.json(); } catch (_) { return ""; }
      const user = data && (data.user || data.current_user || data);
      const id = user && (user.id || user.user_id);
      return id ? String(id) : "";
    } catch (_) {
      return "";
    }
  }

  async function pairIfNeeded() {
    const pair = readPairFromFragment();
    if (!pair) return;
    try {
      await chrome.runtime.sendMessage({ type: "DT_VINTED_PAIR", pair });
      history.replaceState(null, document.title, location.pathname + location.search);
    } catch (_) {}
  }

  let busy = false;
  async function pollLogin() {
    if (busy) return;
    busy = true;
    try {
      const userId = await confirmCurrentUser();
      if (userId) {
        await chrome.runtime.sendMessage({ type: "DT_VINTED_AUTHENTICATED", userId });
      }
    } catch (_) {
    } finally {
      busy = false;
    }
  }

  pairIfNeeded().finally(() => {
    pollLogin();
    setInterval(pollLogin, 2500);
  });
})();
