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
  // Only migrate known UI preferences; recorded provenance is never rewritten.
  function migratedPath(path, aliases) {
    if (typeof path !== "string") return path;
    const entries = Object.entries(aliases || {}).filter(([from, to]) =>
      from.startsWith("/") && typeof to === "string" && to.startsWith("/"));
    entries.sort((a, b) => b[0].length - a[0].length);
    for (const [from, to] of entries) {
      const oldRoot = from.replace(/\/+$/, "");
      if (oldRoot && (path === oldRoot || path.startsWith(oldRoot + "/")))
        return to.replace(/\/+$/, "") + path.slice(oldRoot.length);
    }
    return path;
  }
  function migrateStoredPaths(storage, profile, aliases) {
    const keys = ["cobot-recording-directories", "cobot-data-console-config:" + profile,
      "cobot-recent-paths-v2:" + profile, "cobot-recent-rlt-paths-v2:" + profile,
      "cobot-unified-collection-paths", "cobot-recent-evaluation-paths"];
    for (const key of keys) {
      try {
        const raw = storage.getItem(key);
        if (!raw) continue;
        const old = JSON.parse(raw);
        const updated = Array.isArray(old) ? old.map(path => migratedPath(path, aliases))
          : old && typeof old === "object" && typeof old.data_root === "string"
            ? { ...old, data_root: migratedPath(old.data_root, aliases) } : old;
        if (JSON.stringify(updated) !== JSON.stringify(old))
          storage.setItem(key, JSON.stringify(updated));
      } catch (_error) { /* Blocked/corrupt storage must not prevent page startup. */ }
    }
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
  function recentSelector({input, id, storage, storageKey, extraPaths=()=>[], onSelect}) {
    storage = storage || root.localStorage;
    const label = document.createElement("label");
    label.className = "storage-recent";
    const title = document.createElement("span");
    title.dataset.zh = "最近使用目录"; title.dataset.en = "Recent directories";
    const select = document.createElement("select");
    select.id = id;
    let signature = "";
    function update() {
      const english = root.CobotPreferences?.language === "en";
      title.textContent = english ? title.dataset.en : title.dataset.zh;
      const paths = mergeRecent([...readRecent(storage, storageKey), ...cleanPaths(extraPaths())], null, 12);
      const next = JSON.stringify([english, paths]);
      if (next === signature) return;
      signature = next;
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = paths.length
        ? (english ? "Choose a recent directory…" : "选择最近使用的目录…")
        : (english ? "No recent directories" : "暂无最近使用目录");
      select.replaceChildren(placeholder, ...paths.map(path => {
        const option = document.createElement("option");
        option.value = path; option.textContent = path; option.title = path;
        return option;
      }));
    }
    function remember(path) {
      if (!path) return;
      try { storage.setItem(storageKey, JSON.stringify(mergeRecent(
        readRecent(storage, storageKey), String(path).replace(/\/+$/, ""), 12))); } catch (_) {}
      update();
    }
    select.addEventListener("change", () => {
      const path = select.value;
      if (!path || select.disabled) return;
      input.value = path;
      input.dispatchEvent(new Event("input", {bubbles:true}));
      if (onSelect) onSelect(path);
      select.value = "";
    });
    label.append(title, select);
    (input.closest("label") || input).after(label);
    document.addEventListener("cobot:language", update);
    update();
    return {remember, update, setDisabled: value => {select.disabled = Boolean(value);}};
  }
  const api = { mergeRecent, completeDirectory, pathName, migratedPath, migrateStoredPaths, create, recentSelector };
  root.CobotPathPicker = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
