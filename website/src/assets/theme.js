(() => {
  const storageKey = "aurum-website-theme";
  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
  let preference = null;
  try {
    const stored = window.localStorage.getItem(storageKey);
    if (stored === "light" || stored === "dark") preference = stored;
  } catch {
    // Visual preferences remain usable when browser storage is unavailable.
  }

  const applyTheme = () => {
    const theme = preference ?? (systemTheme.matches ? "dark" : "light");
    document.documentElement.dataset.theme = theme;
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", theme === "dark" ? "#101b20" : "#00566b");
    for (const button of document.querySelectorAll("[data-theme-toggle]")) {
      button.setAttribute("aria-pressed", String(theme === "dark"));
      button.title =
        theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему";
      button.hidden = false;
    }
  };

  // This local head script runs before CSS to avoid a bright first frame.
  applyTheme();
  systemTheme.addEventListener("change", () => {
    if (preference === null) applyTheme();
  });
  window.addEventListener("storage", (event) => {
    if (event.key !== storageKey && event.key !== null) return;
    preference =
      event.newValue === "light" || event.newValue === "dark"
        ? event.newValue
        : null;
    applyTheme();
  });
  document.addEventListener(
    "DOMContentLoaded",
    () => {
      applyTheme();
      for (const button of document.querySelectorAll("[data-theme-toggle]")) {
        button.addEventListener("click", () => {
          preference =
            document.documentElement.dataset.theme === "dark"
              ? "light"
              : "dark";
          try {
            window.localStorage.setItem(storageKey, preference);
          } catch {
            // The selection still applies for the current page.
          }
          applyTheme();
        });
      }
    },
    { once: true },
  );
})();
