(function () {
  try {
    var preference = localStorage.getItem("theme:preference") || "system";
    var dark =
      preference === "dark" ||
      (preference === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  } catch {
    document.documentElement.setAttribute("data-theme", "light");
  }
})();
