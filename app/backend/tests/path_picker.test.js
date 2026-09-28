"use strict";
const assert = require("assert").strict;
const {
  mergeRecent, completeDirectory, pathName, create,
} = require("../segmented_frontend/path_picker.js");

function node() {
  const item = {
    value: "", textContent: "", hidden: true, disabled: false,
    children: [], attrs: {}, listeners: {},
    classes: new Set(),
    classList: {
      add(name) { this.owner.classes.add(name); },
      remove(name) { this.owner.classes.delete(name); },
      toggle(name) { this.owner.classes.has(name) ? this.owner.classes.delete(name) : this.owner.classes.add(name); },
      owner: null,
    },
    replaceChildren(...items) { this.children = items; },
    append(...items) { this.children.push(...items); },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    addEventListener(k, fn) { this.listeners[k] = fn; },
    focus() { this.focused = true; },
  };
  item.classList.owner = item;
  return item;
}
function documentStub() {
  return {
    createElement() {
      const item = node();
      item.dataset = {};
      return item;
    },
  };
}
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const pickerButtons = list => list.children.reduce(
  (buttons, section) => buttons.concat(section.children[1].children), [],
);

test("recent paths are deduplicated, newest first, and capped", () => {
  assert.deepEqual(
    mergeRecent(["/a", "/b", "/a", "/c"], "/b", 3),
    ["/b", "/a", "/c"],
  );
});

test("directory selection always continues into its children", () => {
  assert.equal(completeDirectory("/data/rlt/plug_v2"), "/data/rlt/plug_v2/");
  assert.equal(completeDirectory("/data/rlt/plug_v2/"), "/data/rlt/plug_v2/");
});

test("only the final directory name is presented", () => {
  assert.equal(pathName("/media/agilex/data/rlt/online/"), "online");
  assert.equal(pathName("/"), "/");
});

test("refresh clears the legacy hidden class", async () => {
  global.document = documentStub();
  const input = node(), panel = node(), list = node(), hint = node();
  panel.classList.add("hidden");
  const picker = create({
    input, panel, list, hint,
    storage: { getItem: () => "[]", setItem() {} }, storageKey: "paths",
    fetchDirectories: async () => ({directory:"/data",exists:true,directories:[],truncated:false}),
  });
  input.value = "/data/";
  await picker.refresh();
  assert.equal(panel.classes.has("hidden"), false);
});

test("late directory replies cannot replace a newer query", async () => {
  global.document = documentStub();
  const input = node(), panel = node(), list = node(), hint = node();
  const releases = {};
  const picker = create({
    input, panel, list, hint,
    storage: { getItem: () => "[]", setItem() {} },
    storageKey: "paths",
    fetchDirectories(value) {
      return new Promise(resolve => { releases[value] = resolve; });
    },
  });
  input.value = "/data/o";
  const old = picker.refresh();
  input.value = "/data/new/";
  const latest = picker.refresh();
  releases["/data/new/"]({
    directory: "/data/new", exists: true,
    directories: ["/data/new/child"], truncated: false,
  });
  await latest;
  releases["/data/o"]({
    directory: "/data", exists: true,
    directories: ["/data/old"], truncated: false,
  });
  await old;
  const buttons = pickerButtons(list);
  assert.equal(buttons.length, 1);
  assert.equal(buttons[0].dataset.path, "/data/new/child");
  assert.equal(buttons[0].textContent, "child");
});

test("recent choices and direct children render in separate named groups", async () => {
  global.document = documentStub();
  const input = node(), panel = node(), list = node(), hint = node();
  const picker = create({
    input, panel, list, hint,
    storage: { getItem: () => '["/data/rlt/demo"]', setItem() {} },
    storageKey: "paths",
    fetchDirectories: async () => ({
      directory: "/data/rlt", exists: true,
      directories: ["/data/rlt/online", "/data/rlt/warmup"], truncated: false,
    }),
  });
  input.value = "/data/rlt/";
  await picker.refresh();
  assert.deepEqual(list.children.map(group => group.children[0].textContent), ["最近使用", "直属子目录"]);
  const buttons = pickerButtons(list);
  assert.deepEqual(buttons.map(button => button.textContent), ["demo", "online", "warmup"]);
  assert.deepEqual(buttons.map(button => button.dataset.path), ["/data/rlt/demo", "/data/rlt/online", "/data/rlt/warmup"]);
  assert.equal(hint.textContent, "当前位置：rlt。选择子目录进入下一层。");
});

test("keyboard enter chooses the highlighted match and continues browsing", async () => {
  global.document = documentStub();
  const input = node(), panel = node(), list = node(), hint = node();
  const picker = create({
    input, panel, list, hint,
    storage: { getItem: () => "[]", setItem() {} },
    storageKey: "paths",
    fetchDirectories: async () => ({
      directory: "/data", exists: true,
      directories: ["/data/plug", "/data/test"], truncated: false,
    }),
  });
  input.value = "/data/pl";
  await picker.refresh();
  input.listeners.keydown({ key: "ArrowDown", preventDefault() {} });
  input.listeners.keydown({ key: "Enter", preventDefault() {} });
  assert.equal(input.value, "/data/plug/");
});

(async () => {
  for (const [name, fn] of tests) {
    await fn();
    console.log("PASS " + name);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

test("migration preserves selections and only replaces the registered directory boundary", () => {
  const { migratedPath, migrateStoredPaths } = require("../segmented_frontend/path_picker.js");
  const aliases = {"/old/data":"/new/data"};
  assert.equal(migratedPath("/old/data/online", aliases), "/new/data/online");
  assert.equal(migratedPath("/old/data-other", aliases), "/old/data-other");
  const values = new Map([
    ["cobot-recording-directories", '["/old/data/online","/unrelated"]'],
    ["cobot-data-console-config:p", '{"data_root":"/old/data","other":42}'],
    ["cobot-recent-paths-v2:p", '["/old/data/warmup"]'],
    ["cobot-recent-rlt-paths-v2:p", '["/old/data/online"]'],
    ["unrelated", '["/old/data"]']
  ]);
  const storage = { getItem: key => values.get(key), setItem: (key,v) => values.set(key,v) };
  migrateStoredPaths(storage, "p", aliases);
  assert.deepEqual(JSON.parse(values.get("cobot-recording-directories")), ["/new/data/online","/unrelated"]);
  assert.deepEqual(JSON.parse(values.get("cobot-data-console-config:p")), {data_root:"/new/data",other:42});
  assert.equal(values.get("unrelated"), '["/old/data"]');
  const once = [...values];
  migrateStoredPaths(storage, "p", aliases);
  assert.deepEqual([...values], once);
  assert.doesNotThrow(() => migrateStoredPaths({getItem(){throw Error("blocked");}}, "p", aliases));
});
