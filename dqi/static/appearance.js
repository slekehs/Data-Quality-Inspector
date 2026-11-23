"use strict";

(() => {
  // The launcher may choose a different port next time. A host cookie keeps the
  // preference available to the next session and to locally opened HTML reports.
  function savedChoice() {
    try {
      const match = document.cookie.match(/(?:^|;\s*)dqi_appearance=(system|light|dark)(?=;|$)/);
      return match ? match[1] : null;
    } catch {
      return null;
    }
  }

  const system = window.matchMedia("(prefers-color-scheme: dark)");
  let choice = savedChoice() || "system";
  let visitOnly = false;
  let select;

  function apply() {
    const systemTheme = system.matches ? "dark" : "light";
    document.documentElement.dataset.theme = choice === "system" ? systemTheme : choice;
    if (select) select.value = choice;
  }

  apply();
  system.addEventListener("change", apply);
  window.addEventListener("focus", () => {
    if (!visitOnly) choice = savedChoice() || "system";
    apply();
  });

  document.addEventListener("DOMContentLoaded", () => {
    select = document.getElementById("appearance-choice");
    const help = document.getElementById("appearance-help");
    const savedHelp = help.textContent;
    select.value = choice;
    select.addEventListener("change", () => {
      choice = select.value;
      apply();
      try {
        document.cookie = `dqi_appearance=${choice}; Path=/; Max-Age=31536000; SameSite=Strict`;
      } catch {
        // A browser can reject the write by throwing or by silently ignoring it.
      }
      visitOnly = savedChoice() !== choice;
      help.textContent = visitOnly ? "Your choice applies for this visit. Allow cookies to remember it next time." : savedHelp;
    });
  });
})();
