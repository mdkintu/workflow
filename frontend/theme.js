/* Theme (ADR-19). Loaded without `defer` in <head> so the saved choice applies
 * before first paint. "system" = follow the OS; the toggle stores light/dark
 * in localStorage (a per-device preference, nothing sent to the server). */
(function () {
  var KEY = "wf-theme";
  var root = document.documentElement;
  function saved() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function isDark() {
    var t = root.getAttribute("data-theme");
    if (t) return t === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  var t = saved();
  if (t === "light" || t === "dark") root.setAttribute("data-theme", t);

  window.wfTheme = {
    isDark: isDark,
    toggle: function () {
      var next = isDark() ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* private mode: not remembered */ }
      return next === "dark";
    },
  };
})();
