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
    root.setAttribute("data-density", allowed ? density : "comfortable");
  } catch {
    root.setAttribute("data-density", "comfortable");
  }
})();
