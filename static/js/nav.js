const THEME_STORAGE_KEY = "canikona:theme";

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
}

function loadInitialTheme() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "dark" || stored === "light") {
      applyTheme(stored);
      return;
    }
  } catch (_) {}

  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(prefersDark ? "dark" : "light");
}

document.addEventListener("DOMContentLoaded", () => {
  loadInitialTheme();

  const navToggle = document.querySelector(".nav-toggle");
  const navMenu = document.querySelector(".nav-menu");
  const body = document.body;
  const themeToggle = document.getElementById("theme-toggle");

  if (navToggle && navMenu) {
    navToggle.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      navMenu.classList.toggle("active");
      body.classList.toggle("menu-open");
      navToggle.classList.toggle("active");
    });

    document.addEventListener("click", (event) => {
      if (!event.target.closest(".nav-menu") && !event.target.closest(".nav-toggle") && navMenu.classList.contains("active")) {
        navMenu.classList.remove("active");
        body.classList.remove("menu-open");
        navToggle.classList.remove("active");
      }
    });
  }

  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "light";
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
      try {
        localStorage.setItem(THEME_STORAGE_KEY, next);
      } catch (_) {}
      window.dispatchEvent(new CustomEvent("canikona:theme-changed", { detail: { theme: next } }));
    });
  }
});
