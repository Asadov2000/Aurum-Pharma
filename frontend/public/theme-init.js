(function () {
  var root = document.documentElement;
  try {
    var preference = localStorage.getItem("theme:preference") || "system";
    var dark =
      preference === "dark" ||
      (preference === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    root.setAttribute("data-theme", dark ? "dark" : "light");
  } catch {
    root.setAttribute("data-theme", "light");
  }

  try {
    var density = localStorage.getItem("ui:density");
    var allowed = density === "compact" || density === "comfortable" || density === "touch";
    var automatic = density === "auto";
    var coarse = window.matchMedia("(pointer: coarse)").matches;
    root.setAttribute("data-density", allowed ? density : automatic && coarse ? "touch" : "comfortable");
  } catch {
    root.setAttribute("data-density", "comfortable");
  }

  try {
    var contrast = localStorage.getItem("ui:contrast");
    var accent = localStorage.getItem("ui:accent");
    var allowedAccent = ["teal", "blue", "violet", "green", "amber", "rose"].includes(accent);
    root.setAttribute("data-contrast", contrast === "high" ? "high" : "standard");
    root.setAttribute("data-accent", allowedAccent ? accent : "teal");
    root.setAttribute(
      "data-reduce-motion",
      localStorage.getItem("ui:reduce-motion") === "1" ? "true" : "false",
    );
  } catch {
    root.setAttribute("data-contrast", "standard");
    root.setAttribute("data-accent", "teal");
    root.setAttribute("data-reduce-motion", "false");
  }
})();
