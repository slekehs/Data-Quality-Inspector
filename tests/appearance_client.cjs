const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(process.argv[2], "appearance.js"), "utf8");

function openPage({ dark = false, cookies = { value: "" }, blocked = false } = {}) {
  const system = new EventTarget();
  system.matches = dark;
  const window = new EventTarget();
  window.matchMedia = (query) => {
    assert.equal(query, "(prefers-color-scheme: dark)");
    return system;
  };

  const select = new EventTarget();
  const document = new EventTarget();
  document.documentElement = { dataset: {} };
  const help = { textContent: "Saved in this browser." };
  document.getElementById = (id) => {
    if (id === "appearance-help") return help;
    assert.equal(id, "appearance-choice");
    return select;
  };
  Object.defineProperty(document, "cookie", {
    get() {
      if (blocked) throw new Error("Cookies are unavailable");
      return cookies.value;
    },
    set(value) {
      if (blocked) throw new Error("Cookies are unavailable");
      cookies.value = value.split(";")[0];
    },
  });

  vm.runInNewContext(script, { window, document }, { filename: "appearance.js" });
  const initialTheme = document.documentElement.dataset.theme;
  document.dispatchEvent(new Event("DOMContentLoaded"));
  return {
    initialTheme,
    expect(theme, choice) {
      assert.equal(document.documentElement.dataset.theme, theme);
      assert.equal(select.value, choice);
    },
    setSystem(isDark) {
      system.matches = isDark;
      system.dispatchEvent(new Event("change"));
    },
    choose(choice) {
      select.value = choice;
      select.dispatchEvent(new Event("change"));
    },
    focus() {
      window.dispatchEvent(new Event("focus"));
    },
  };
}

const cookies = { value: "" };
const page = openPage({ cookies });
assert.equal(page.initialTheme, "light");
page.expect("light", "system");
page.setSystem(true);
page.expect("dark", "system");
page.choose("light");
page.expect("light", "light");
const restored = openPage({ dark: true, cookies });
assert.equal(restored.initialTheme, "light");
restored.expect("light", "light");
restored.setSystem(false);
restored.setSystem(true);
restored.expect("light", "light");
restored.choose("dark");
restored.setSystem(false);
restored.expect("dark", "dark");
page.focus(); // Another tab selected Dark.
page.expect("dark", "dark");
const reopened = openPage({ cookies });
assert.equal(reopened.initialTheme, "dark");
reopened.expect("dark", "dark");
reopened.choose("system");
reopened.expect("light", "system");
reopened.setSystem(true);
reopened.expect("dark", "system");
openPage({ cookies }).expect("light", "system");

const malformed = { value: "other_dqi_appearance=light; dqi_appearance=light_extra" };
openPage({ dark: true, cookies: malformed }).expect("dark", "system");
const amongOtherCookies = { value: "unrelated=1; dqi_appearance=dark; example=2" };
openPage({ cookies: amongOtherCookies }).expect("dark", "dark");

const privatePage = openPage({ blocked: true });
privatePage.expect("light", "system");
privatePage.choose("dark");
privatePage.focus();
privatePage.setSystem(false);
privatePage.expect("dark", "dark");
