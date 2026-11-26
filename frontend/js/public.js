import { startLogin, startSignup } from "./auth.js";

function initPublicSite() {
  // Forward any OAuth callback landing on a public page straight to portal.html before exchange.
  const search = new URLSearchParams(window.location.search);
  if (search.has("code") || search.has("error")) {
    window.location.replace(`/portal.html${window.location.search}`);
    return;
  }

  const menuBtn = document.getElementById("pub-menu-toggle");
  const drawer = document.getElementById("pub-mobile-drawer");
  if (menuBtn && drawer) {
    menuBtn.addEventListener("click", () => {
      const open = drawer.classList.toggle("open");
      menuBtn.setAttribute("aria-expanded", String(open));
    });
  }

  document.querySelectorAll('[data-auth-action="login"]').forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      event.preventDefault();
      btn.setAttribute("disabled", "true");
      try {
        await startLogin();
      } catch (_err) {
        btn.removeAttribute("disabled");
      }
    });
  });

  document.querySelectorAll('[data-auth-action="signup"]').forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      event.preventDefault();
      btn.setAttribute("disabled", "true");
      try {
        await startSignup();
      } catch (_err) {
        btn.removeAttribute("disabled");
      }
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initPublicSite);
} else {
  initPublicSite();
}
