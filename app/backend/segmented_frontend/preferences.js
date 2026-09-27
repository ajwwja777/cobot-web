"use strict";

(function expose(root) {
  const STORAGE_KEY = "cobot-console-preferences-v1";
  const DEFAULTS = { language: "zh", theme: "light" };
  let state = { ...DEFAULTS };
  function english(value) {
    return root.CobotLocaleCatalog ? root.CobotLocaleCatalog.english(value) : String(value ?? "");
  }

  function translateDynamic(scope) {
    root.CobotDashboardLocale?.walk(scope?.nodeType === 9 ? scope.body : scope || document.body);
  }

  function read() {
    try {
      const parsed = JSON.parse(root.localStorage.getItem(STORAGE_KEY) || "{}");
      return {
        language: parsed.language === "en" ? "en" : "zh",
        theme: parsed.theme === "dark" ? "dark" : "light",
      };
    } catch (_error) { return { ...DEFAULTS }; }
  }

  function save() {
    try { root.localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (_error) {}
  }

  function translateNode(node) {
    if (!node || !node.dataset) return;
    const language = state.language;
    if (node.dataset.zh != null && node.dataset.en != null) node.textContent = node.dataset[language];
    if (node.dataset.zhPlaceholder != null && node.dataset.enPlaceholder != null) {
      node.placeholder = node.dataset[language + "Placeholder"];
    }
    if (node.dataset.zhAria != null && node.dataset.enAria != null) {
      node.setAttribute("aria-label", node.dataset[language + "Aria"]);
    }
  }

  function apply() {
    document.documentElement.dataset.theme = state.theme;
    document.documentElement.lang = state.language === "en" ? "en" : "zh-CN";
    document.querySelectorAll("[data-zh][data-en], [data-zh-placeholder][data-en-placeholder], [data-zh-aria][data-en-aria]").forEach(translateNode);
    document.querySelectorAll("[data-language-choice]").forEach(button => {
      button.classList.toggle("selected", button.dataset.languageChoice === state.language);
      button.setAttribute("aria-pressed", String(button.dataset.languageChoice === state.language));
    });
    document.querySelectorAll("[data-theme-choice]").forEach(button => {
      button.classList.toggle("selected", button.dataset.themeChoice === state.theme);
      button.setAttribute("aria-pressed", String(button.dataset.themeChoice === state.theme));
    });
    // The shared locale observer owns dynamic text and remembers its source.
    // Replacing whole textContent here loses the Chinese original and races it.
    root.CobotDashboardLocale?.walk(document.body);
  }

  function setLanguage(language) {
    state.language = language === "en" ? "en" : "zh";
    save(); apply();
    document.dispatchEvent(new CustomEvent("cobot:language", { detail: { language: state.language } }));
  }
  function setTheme(theme) { state.theme = theme === "dark" ? "dark" : "light"; save(); apply(); }

  function mount() {
    state = read();
    const button = document.getElementById("settings-open");
    const drawer = document.getElementById("settings-drawer");
    const close = document.getElementById("settings-close");
    if (button && drawer) button.addEventListener("click", () => { drawer.hidden = false; button.setAttribute("aria-expanded", "true"); });
    if (close && drawer && button) close.addEventListener("click", () => { drawer.hidden = true; button.setAttribute("aria-expanded", "false"); button.focus(); });
    document.querySelectorAll("[data-language-choice]").forEach(node => node.addEventListener("click", () => setLanguage(node.dataset.languageChoice)));
    document.querySelectorAll("[data-theme-choice]").forEach(node => node.addEventListener("click", () => setTheme(node.dataset.themeChoice)));
    apply();
  }

  const api = { get language() { return state.language; }, get theme() { return state.theme; }, read, apply, setLanguage, setTheme, english, text:value=>state.language==='en'?english(value):value, translateDynamic, mount };
  root.CobotPreferences = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
    else mount();
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
