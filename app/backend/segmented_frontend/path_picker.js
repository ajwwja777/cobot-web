"use strict";

(function expose(root) {
  function cleanPaths(paths) {
    return (Array.isArray(paths) ? paths : [])
      .filter(path => typeof path === "string" && path.trim())
      .map(path => path.trim());
  }
  function mergeRecent(paths, selected, maximum = 12) {
    const ordered = selected ? [selected, ...cleanPaths(paths)] : cleanPaths(paths);
    return [...new Set(ordered)].slice(0, maximum);
  }
  function completeDirectory(path) {
    const value = String(path || "").trim();
    return value.endsWith("/") ? value : value + "/";
  }
  function pathName(path) {
    const value = String(path || "").replace(/\/+$/, "");
    const pieces = value.split("/").filter(Boolean);
    return pieces.length ? pieces[pieces.length - 1] : "/";
  }
  function readRecent(storage, key) {
    try { return cleanPaths(JSON.parse(storage.getItem(key) || "[]")); }
    catch (_error) { return []; }
  }
  function create(options) {
    const { input, panel, list, hint, fetchDirectories } = options;
    const storage = options.storage || root.localStorage;
    const storageKey = options.storageKey || "cobot-paths";
    let token = 0, active = -1, disabled = false, candidates = [], optionNodes = [];

    function recent() { return readRecent(storage, storageKey); }
    function remember(path) {
      const paths = mergeRecent(recent(), String(path || "").replace(/\/$/, ""), 12);
      storage.setItem(storageKey, JSON.stringify(paths));
      return paths;
    }
    function choose(path) {
      if (disabled) return;
      input.value = completeDirectory(path);
      active = -1;
      if (typeof options.onChange === "function") options.onChange(input.value);
      refresh();
      input.focus();
    }
    function row(path, kind, label) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "path-option";
      button.dataset.path = path;
      button.dataset.kind = kind;
      button.setAttribute("role", "option");
      button.setAttribute("aria-label", "选择目录 " + label);
      button.textContent = label;
      button.addEventListener("click", () => choose(path));
      return button;
    }
    function group(title, items) {
      const section = document.createElement("section");
      section.className = "path-group";
      const heading = document.createElement("p");
      heading.className = "path-group-title";
      heading.textContent = title;
      const options = document.createElement("div");
      options.className = "path-group-options";
      for (const item of items) {
        const button = row(item.path, item.kind, item.label);
        optionNodes.push(button);
        options.append(button);
      }
      section.append(heading, options);
      return section;
    }
    function render(paths, result) {
      const recents = recent().slice(0, 6).map(path => ({
        path, kind: "recent", label: pathName(path),
      }));
      const directories = cleanPaths(paths).map(path => ({
        path, kind: "directory", label: pathName(path),
      }));
      candidates = [...recents, ...directories];
      optionNodes = [];
      const groups = [];
      if (recents.length) groups.push(group("最近使用", recents));
      groups.push(group("直属子目录", directories));
      list.replaceChildren(...groups);
      active = -1;
      panel.hidden = false;
      panel.classList.remove("hidden");
      if (!result.exists) {
        hint.textContent = "此目录尚不存在；确认后将安全创建。";
      } else if (directories.length) {
        hint.textContent = "当前位置：" + pathName(result.directory) + "。选择子目录进入下一层。";
      } else {
        hint.textContent = "当前位置：" + pathName(result.directory) + "。没有匹配的直属子目录。";
      }
    }
    function paintActive() {
      optionNodes.forEach((item, index) => {
        item.setAttribute("aria-selected", index === active ? "true" : "false");
      });
    }
    async function refresh() {
      if (disabled) return;
      const value = input.value.trim();
      const current = ++token;
      panel.hidden = false;
      panel.classList.remove("hidden");
      hint.textContent = "正在搜索目录…";
      try {
        const result = await fetchDirectories(value);
        if (current !== token || value !== input.value.trim()) return;
        render(result.directories || [], result);
      } catch (error) {
        if (current !== token || value !== input.value.trim()) return;
        list.replaceChildren(); candidates = [];
        hint.textContent = "目录读取失败：" + error.message;
      }
    }
    function close() { token++; panel.hidden = true; active = -1; }
    function setDisabled(value) {
      disabled = Boolean(value);
      input.disabled = disabled;
      if (disabled) close();
    }
    input.addEventListener("keydown", event => {
      if (event.key === "Escape") { close(); return; }
      if (!candidates.length) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const delta = event.key === "ArrowDown" ? 1 : -1;
        active = (active + delta + candidates.length) % candidates.length;
        paintActive();
      } else if (event.key === "Enter" && active >= 0) {
        event.preventDefault();
        choose(candidates[active].path);
      }
    });
    input.addEventListener("focus", refresh);
    input.addEventListener("input", () => {
      if (typeof options.onChange === "function") options.onChange(input.value);
      refresh();
    });
    return { refresh, remember, close, setDisabled, recent };
  }
  const api = { mergeRecent, completeDirectory, pathName, create };
  root.CobotPathPicker = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
