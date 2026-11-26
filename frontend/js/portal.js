import { createApiClient } from "./api.js";
import {
  clearMemorySession,
  getRemainingSeconds,
  getSession,
  handleCallbackIfPresent,
  isExpiringSoon,
  signOut,
  startLogin,
  startSignup
} from "./auth.js";
import { alertBanner, clear, createIcon, el, showToast } from "./ui.js";
import { renderAppointmentsView } from "./views/appointments.js";
import { renderFilesView } from "./views/files.js";
import { renderGovernanceView } from "./views/governance.js";
import { renderNotesView } from "./views/notes.js";
import { renderOverviewView } from "./views/overview.js";
import { renderPrivacyView } from "./views/privacy.js";

const ROLE_LABELS = {
  patient: "Patient Care",
  doctor: "Attending Physician",
  staff: "Clinic Operations",
  admin: "Compliance & Governance",
  unassigned: "Account Pending Assignment"
};

const NAV_BY_ROLE = {
  patient: [
    { id: "overview", label: "Overview", icon: "home" },
    { id: "appointments", label: "Appointments", icon: "calendar" },
    { id: "notes", label: "Visit Summaries", icon: "notes" },
    { id: "files", label: "Lab Documents", icon: "files" },
    { id: "privacy", label: "Profile & Privacy", icon: "user" }
  ],
  doctor: [
    { id: "overview", label: "Workbench", icon: "home" },
    { id: "appointments", label: "My Schedule", icon: "calendar" },
    { id: "notes", label: "Patient Charts", icon: "notes" },
    { id: "files", label: "Lab Results", icon: "files" }
  ],
  staff: [
    { id: "overview", label: "Front Desk", icon: "home" },
    { id: "appointments", label: "Clinic Schedule", icon: "calendar" },
    { id: "files", label: "Lab Intake", icon: "files" }
  ],
  admin: [
    { id: "overview", label: "Overview", icon: "home" },
    { id: "governance", label: "Audit & Assignments", icon: "shield" },
    { id: "appointments", label: "Clinic Oversight", icon: "calendar" },
    { id: "files", label: "Document Metadata", icon: "files" }
  ]
};

const VIEW_RENDERERS = {
  overview: renderOverviewView,
  appointments: renderAppointmentsView,
  notes: renderNotesView,
  files: renderFilesView,
  privacy: renderPrivacyView,
  governance: renderGovernanceView
};

let currentRoute = "overview";
let sessionTimerId = null;

function renderAuthGate(root, errorMessage = "") {
  clear(root);
  if (sessionTimerId) {
    window.clearInterval(sessionTimerId);
    sessionTimerId = null;
  }

  const card = el("div", { className: "auth-gate-card" }, [
    el("a", { className: "brand", href: "index.html" }, [
      el("img", { className: "brand-mark", src: "assets/images/logo.svg", alt: "", width: "34", height: "34" }),
      el("span", {}, [
        el("span", { className: "brand-name", text: "SecureCare Health" }),
        el("span", { className: "brand-sub", text: "Clinical Portal" })
      ])
    ]),
    el("div", {}, [
      el("h1", { text: "Sign in to access your care workspace" }),
      el("p", {
        style: "margin:0.5rem 0 0;color:var(--sc-text-secondary);font-size:0.9375rem;",
        text: "Continue with your SecureCare account to view appointments, clinical notes, and laboratory records."
      })
    ])
  ]);

  if (errorMessage) {
    card.appendChild(alertBanner(errorMessage, "error"));
  }

  card.appendChild(el("div", { style: "display:flex;flex-direction:column;gap:0.65rem;" }, [
    el("button", {
      type: "button",
      className: "btn btn-primary btn-lg",
      text: "Sign In",
      onClick: () => startLogin()
    }),
    el("button", {
      type: "button",
      className: "btn btn-secondary",
      text: "Create Account",
      onClick: () => startSignup()
    }),
    el("a", {
      className: "btn btn-ghost",
      href: "index.html",
      text: "Return to Homepage"
    })
  ]));

  const mediaPane = el("div", { className: "auth-gate-media" }, [
    el("img", {
      src: "assets/images/hero-clinic-care.jpg",
      alt: "SecureCare Health outpatient care lounge"
    }),
    el("div", { className: "auth-gate-media-scrim" }, [
      el("h2", { text: "Coordinated Clinical Care" }),
      el("p", { text: "Private consultation summaries, diagnostic results, and scheduling in one protected portal." })
    ])
  ]);

  root.appendChild(el("div", { className: "auth-gate" }, [
    el("div", { className: "auth-gate-split" }, [mediaPane, card])
  ]));
}

function renderUnassignedGate(root, session) {
  clear(root);
  const displayName = session.claims.name || session.claims.email || "Member";
  const card = el("div", { className: "auth-gate-card" }, [
    el("a", { className: "brand", href: "index.html" }, [
      el("img", { className: "brand-mark", src: "assets/images/logo.svg", alt: "", width: "34", height: "34" }),
      el("span", {}, [
        el("span", { className: "brand-name", text: "SecureCare Health" }),
        el("span", { className: "brand-sub", text: "Clinical Portal" })
      ])
    ]),
    el("h1", { text: `Welcome, ${displayName}` }),
    alertBanner(
      "Your account has been authenticated, but a clinic role has not yet been assigned. Please contact support@outship.me to complete onboarding.",
      "info"
    ),
    el("button", {
      type: "button",
      className: "btn btn-secondary",
      text: "Sign Out",
      onClick: () => signOut()
    })
  ]);
  root.appendChild(el("div", { className: "auth-gate" }, [card]));
}

function renderPortalShell(root, session) {
  clear(root);
  const navItems = NAV_BY_ROLE[session.role] || [];
  if (!navItems.some((item) => item.id === currentRoute)) {
    currentRoute = navItems[0]?.id || "overview";
  }

  const displayName = session.claims.name || session.claims.email || session.claims["cognito:username"] || "Portal User";
  const displayEmail = session.claims.email || "";

  const api = createApiClient({
    onUnauthorized: () => {
      clearMemorySession();
      showToast("Your session expired. Please sign in again.", "error");
      renderAuthGate(root, "Your session has expired. Please sign in again.");
    }
  });

  const rail = el("aside", { className: "portal-rail", "aria-label": "Portal navigation" });
  const railBackdrop = el("div", {
    className: "rail-backdrop",
    hidden: true,
    onClick: () => closeRail()
  });
  const navEl = el("nav", { className: "rail-nav" });
  const topTitle = el("h1", { className: "portal-page-title" });
  const topSub = el("p", { className: "portal-page-sub", text: ROLE_LABELS[session.role] || "" });
  const expirySlot = el("div");
  const contentArea = el("div", { className: "portal-content" });

  function openRail() {
    rail.classList.add("open");
    railBackdrop.hidden = false;
  }

  function closeRail() {
    rail.classList.remove("open");
    railBackdrop.hidden = true;
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && rail.classList.contains("open")) {
      closeRail();
    }
  });

  function buildNav() {
    clear(navEl);
    for (const item of navItems) {
      const btn = el("button", {
        type: "button",
        className: "rail-nav-btn",
        "aria-current": item.id === currentRoute ? "page" : null,
        onClick: () => {
          closeRail();
          navigate(item.id);
        }
      }, [createIcon(item.icon, 16), el("span", { text: item.label })]);
      navEl.appendChild(btn);
    }
  }

  async function navigate(routeId) {
    currentRoute = routeId;
    const item = navItems.find((n) => n.id === routeId) || navItems[0];
    topTitle.textContent = item ? item.label : "Workspace";
    buildNav();

    const renderer = VIEW_RENDERERS[currentRoute] || renderOverviewView;
    await renderer(contentArea, { api, session, navigate });
  }

  rail.appendChild(el("div", { className: "rail-top" }, [
    el("a", { className: "brand rail-brand", href: "index.html" }, [
      el("img", { className: "brand-mark", src: "assets/images/logo.svg", alt: "", width: "34", height: "34" }),
      el("span", {}, [
        el("span", { className: "brand-name", style: "color:#FAF8F5;", text: "SecureCare" }),
        el("span", { className: "brand-sub", text: "Clinical Portal" })
      ])
    ]),
    navEl
  ]));

  rail.appendChild(el("div", { className: "rail-footer" }, [
    el("div", { className: "rail-user" }, [
      el("div", { className: "rail-user-name", text: displayName }),
      el("div", { className: "rail-user-meta", text: displayEmail })
    ]),
    el("button", {
      type: "button",
      className: "rail-signout-btn",
      onClick: () => signOut()
    }, [createIcon("logout", 15), el("span", { text: "Sign Out" })])
  ]));

  const mobileNavBtn = el("button", {
    type: "button",
    className: "portal-mobile-nav-btn",
    "aria-label": "Toggle portal navigation",
    onClick: () => (rail.classList.contains("open") ? closeRail() : openRail())
  }, [
    el("span", { text: "Menu" })
  ]);

  const topbar = el("header", { className: "portal-topbar" }, [
    el("div", { className: "portal-topbar-left" }, [
      mobileNavBtn,
      el("div", {}, [topTitle, topSub])
    ]),
    el("div", { className: "portal-topbar-right" }, [
      el("a", { className: "btn btn-ghost btn-sm", href: "index.html", text: "Clinic Home" })
    ])
  ]);

  const mainCol = el("div", { className: "portal-main" }, [topbar, expirySlot, contentArea]);
  root.appendChild(el("div", { className: "portal-shell" }, [railBackdrop, rail, mainCol]));

  if (sessionTimerId) window.clearInterval(sessionTimerId);
  sessionTimerId = window.setInterval(() => {
    const active = getSession();
    if (!active) {
      renderAuthGate(root, "Your session has expired. Please sign in again.");
      return;
    }
    clear(expirySlot);
    if (isExpiringSoon()) {
      const mins = Math.max(1, Math.ceil(getRemainingSeconds() / 60));
      expirySlot.appendChild(el("div", { className: "expiry-banner" }, [
        el("span", { text: `Your clinical session will expire in about ${mins} minute(s).` }),
        el("button", {
          type: "button",
          className: "btn btn-secondary btn-sm",
          text: "Refresh Sign-In",
          onClick: () => startLogin()
        })
      ]));
    }
  }, 15000);

  navigate(currentRoute);
}

async function initPortal() {
  const root = document.getElementById("portal-root");
  if (!root) return;

  try {
    await handleCallbackIfPresent();
  } catch (err) {
    renderAuthGate(root, err.message);
    return;
  }

  const session = getSession();
  if (!session) {
    renderAuthGate(root);
    return;
  }
  if (session.role === "unassigned") {
    renderUnassignedGate(root, session);
    return;
  }
  renderPortalShell(root, session);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initPortal);
} else {
  initPortal();
}
