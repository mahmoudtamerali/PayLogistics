/**
 * XSS-safe DOM helpers, SVG icon builder, modals, and toast notifications.
 * Never interpolates dynamic values into innerHTML.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

const ICON_PATHS = {
  home: [
    ["path", { d: "m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" }],
    ["polyline", { points: "9 22 9 12 15 12 15 22" }]
  ],
  calendar: [
    ["rect", { x: "3", y: "4", width: "18", height: "18", rx: "2" }],
    ["line", { x1: "16", y1: "2", x2: "16", y2: "6" }],
    ["line", { x1: "8", y1: "2", x2: "8", y2: "6" }],
    ["line", { x1: "3", y1: "10", x2: "21", y2: "10" }]
  ],
  notes: [
    ["path", { d: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" }],
    ["polyline", { points: "14 2 14 8 20 8" }],
    ["line", { x1: "16", y1: "13", x2: "8", y2: "13" }],
    ["line", { x1: "16", y1: "17", x2: "8", y2: "17" }]
  ],
  files: [
    ["path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }],
    ["polyline", { points: "17 8 12 3 7 8" }],
    ["line", { x1: "12", y1: "3", x2: "12", y2: "15" }]
  ],
  download: [
    ["path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }],
    ["polyline", { points: "7 10 12 15 17 10" }],
    ["line", { x1: "12", y1: "15", x2: "12", y2: "3" }]
  ],
  shield: [
    ["path", { d: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" }],
    ["path", { d: "m9 12 2 2 4-4" }]
  ],
  user: [
    ["path", { d: "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" }],
    ["circle", { cx: "12", cy: "7", r: "4" }]
  ],
  logout: [
    ["path", { d: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" }],
    ["polyline", { points: "16 17 21 12 16 7" }],
    ["line", { x1: "21", y1: "12", x2: "9", y2: "12" }]
  ],
  close: [
    ["line", { x1: "18", y1: "6", x2: "6", y2: "18" }],
    ["line", { x1: "6", y1: "6", x2: "18", y2: "18" }]
  ],
  refresh: [
    ["polyline", { points: "23 4 23 10 17 10" }],
    ["path", { d: "M20.49 15a9 9 0 1 1-2.12-9.36L23 10" }]
  ]
};

export function createIcon(name, size = 16) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");

  const shapes = ICON_PATHS[name] || ICON_PATHS.notes;
  for (const [tag, attrs] of shapes) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      node.setAttribute(k, v);
    }
    svg.appendChild(node);
  }
  return svg;
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, val] of Object.entries(attrs)) {
    if (val === null || val === undefined || val === false) continue;
    if (key === "className") {
      node.className = val;
    } else if (key === "text") {
      node.textContent = String(val);
    } else if (key === "dataset" && typeof val === "object") {
      for (const [dk, dv] of Object.entries(val)) {
        node.dataset[dk] = String(dv);
      }
    } else if (key.startsWith("on") && typeof val === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), val);
    } else if (val === true) {
      node.setAttribute(key, "");
    } else {
      node.setAttribute(key, String(val));
    }
  }
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === null || child === undefined || child === false) continue;
    if (typeof child === "string" || typeof child === "number") {
      node.appendChild(document.createTextNode(String(child)));
    } else {
      node.appendChild(child);
    }
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

export function statusBadge(label, variant = "neutral") {
  return el("span", { className: `badge badge-${variant}`, text: label });
}

export function alertBanner(message, variant = "info") {
  return el("div", { className: `alert-inline alert-${variant}`, role: "status" }, [
    el("span", { text: message })
  ]);
}

export function emptyState(title, message, iconName = "notes") {
  return el("div", { className: "empty-state" }, [
    el("div", { className: "icon-box", style: "margin:0 auto;" }, [createIcon(iconName, 20)]),
    el("h4", { text: title }),
    el("p", { text: message })
  ]);
}

export function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });
}

export function formatBytes(bytes) {
  const num = Number(bytes);
  if (!Number.isFinite(num) || num <= 0) return "0 B";
  if (num < 1024) return `${num} B`;
  if (num < 1024 * 1024) return `${(num / 1024).toFixed(1)} KB`;
  return `${(num / (1024 * 1024)).toFixed(2)} MB`;
}

export function todayUtcDay() {
  return new Date().toISOString().slice(0, 10);
}

export function showToast(message, type = "success") {
  let region = document.getElementById("sc-toast-region");
  if (!region) {
    region = el("div", { id: "sc-toast-region", className: "toast-region", "aria-live": "polite" });
    document.body.appendChild(region);
  }
  const closeBtn = el("button", {
    type: "button",
    className: "btn btn-ghost btn-sm",
    "aria-label": "Dismiss notification",
    onClick: () => toast.remove()
  }, [createIcon("close", 14)]);

  const toast = el("div", { className: `toast ${type}` }, [
    el("span", { text: message }),
    closeBtn
  ]);
  region.appendChild(toast);
  window.setTimeout(() => {
    if (toast.parentNode) toast.remove();
  }, 5000);
}

export function openModal({ title, body, footerButtons = [] }) {
  const backdrop = el("div", { className: "modal-backdrop", role: "dialog", "aria-modal": "true" });
  const close = () => {
    document.removeEventListener("keydown", onKey);
    backdrop.remove();
  };
  const onKey = (e) => {
    if (e.key === "Escape") close();
  };
  document.addEventListener("keydown", onKey);

  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) close();
  });

  const head = el("div", { className: "modal-head" }, [
    el("h3", { text: title }),
    el("button", {
      type: "button",
      className: "btn btn-ghost btn-sm",
      "aria-label": "Close dialog",
      onClick: close
    }, [createIcon("close", 16)])
  ]);

  const bodyWrap = el("div", { className: "modal-body" }, body);
  const foot = el("div", { className: "modal-foot" }, footerButtons.map((cfg) =>
    el("button", {
      type: "button",
      className: cfg.className || "btn btn-secondary",
      text: cfg.label,
      onClick: () => cfg.onClick(close)
    })
  ));

  const card = el("div", { className: "modal-card" }, [head, bodyWrap, foot]);
  backdrop.appendChild(card);
  document.body.appendChild(backdrop);
  return close;
}
