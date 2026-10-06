const SHAPES = [
  '<svg viewBox="0 0 24 24" width="24" height="24"><path d="M12 3 22 21H2z" fill="#fff"/></svg>',
  '<svg viewBox="0 0 24 24" width="24" height="24"><path d="M12 2 22 12 12 22 2 12z" fill="#fff"/></svg>',
  '<svg viewBox="0 0 24 24" width="24" height="24"><circle cx="12" cy="12" r="10" fill="#fff"/></svg>',
  '<svg viewBox="0 0 24 24" width="24" height="24"><rect x="3" y="3" width="18" height="18" fill="#fff"/></svg>',
];

const money = (n) =>
  "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });

const fmtTime = (s) => {
  s = Math.max(0, Math.floor(s));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function toast(msg, ms = 2600) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

// WebSocket with auto-reconnect. onOpen runs on every (re)connect.
function connect(path, { onOpen, onMessage }) {
  const api = { ws: null, send: (m) => api.ws && api.ws.readyState === 1 && api.ws.send(JSON.stringify(m)) };
  const go = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    api.ws = new WebSocket(`${proto}://${location.host}${path}`);
    api.ws.onopen = () => onOpen && onOpen();
    api.ws.onmessage = (e) => onMessage(JSON.parse(e.data));
    api.ws.onclose = () => setTimeout(go, 1000);
  };
  go();
  return api;
}

// Mirror of the server's payout weighting, for live display.
const weightNow = (m, t, bonus) => {
  const e = Math.min(1, Math.max(0, (m.lock_at - t) / (m.lock_at - m.open_at)));
  return 1 + bonus * e;
};
