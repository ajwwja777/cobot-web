"use strict";
(() => {
  const $ = selector => document.querySelector(selector);
  const en = () => window.CobotPreferences?.language === "en";
  const text = (zh, english) => en() ? english : zh;
  const node = (tag, zh, english) => {
    const n = document.createElement(tag);
    if (zh !== undefined) {
      n.textContent = text(zh, english || zh);
      n.dataset.zh = zh; n.dataset.en = english || zh;
    }
    return n;
  };
  let dialog, data, browserTarget, lastBrowse = "", working = false;
  async function api(path, body) {
    const response = await fetch(path, {cache:"no-store", ...(body ? {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)
    } : {})});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail));
    return result;
  }
  function notice(message, error = false) {
    const target = $("#site-feedback");
    target.textContent = message; target.classList.toggle("error", error);
  }
  async function act(callback) {
    if (working) return;
    working = true;
    dialog.setAttribute("aria-busy", "true");
    try { await callback(); }
    catch(error) { notice(en() ? error.message.split(" / ")[0] : error.message, true); }
    finally { working = false; dialog.removeAttribute("aria-busy"); }
  }
  function button(zh, english, callback) {
    const b = node("button", zh, english); b.type = "button"; b.addEventListener("click", callback); return b;
  }
  function field(parent, id, zh, english, tag = "input") {
    const label = node("label", zh, english), input = node(tag);
    input.id = id; label.htmlFor = id; const row = node("div"); row.className = "site-field";
    row.append(label, input); parent.append(row); return input;
  }
  function pathField(parent, id, zh, english) {
    const input = field(parent, id, zh, english);
    input.parentElement.classList.add("site-path");
    input.parentElement.append(button("浏览", "Browse", () => act(() => browse(id))));
    return input;
  }
  function fill(select, rows, valueKey, label) {
    const previous = select.value;
    select.replaceChildren(...rows.map(row => {
      const option = node("option"); option.value = row[valueKey]; option.textContent = label(row); return option;
    }));
    if (rows.some(row => row[valueKey] === previous)) select.value = previous;
  }
  function renderHardware() {
    const component = $("#site-device").value;
    const config = data.hardware[component] || {};
    $("#site-launch").value = config.path || data.defaults[component];
    $("#site-setup").value = config.setup || "";
    $("#site-cwd").value = config.cwd || "";
    $("#site-args").value = (config.args || []).join("\n");
  }
  function selectAsset() {
    const model = data.models.find(m => m.id === $("#site-assets").value);
    if (!model) return;
    $("#site-checkpoint").value = model.checkpoint || "";
    $("#site-label").value = model.label || "";
    $("#site-asset-status").textContent = model.available
      ? text("已登记，可手动加载", "Registered; load manually")
      : (en() ? (model.unavailable_reason_en || model.unavailable_reason || "").split(" / ").pop() : model.unavailable_reason);
    const template = model.adapter_id || model.id;
    if (data.templates.some(t => t.id === template)) $("#site-template").value = template;
    $("#site-compatible").checked = false;
  }
  async function refresh() {
    data = await api("/api/site/options");
    fill($("#site-assets"), data.models, "id", m => m.checkpoint + (m.available ? "" : text(" [需适配]", " [adapter required]")));
    fill($("#site-template"), data.templates, "id", m => m.label);
    const available = data.models.filter(m => m.available);
    fill($("#site-default"), available, "id", m => m.checkpoint);
    if (available.some(m => m.id === data.selected_model)) $("#site-default").value = data.selected_model;
    renderHardware(); selectAsset();
  }
  async function browse(target, path = "") {
    browserTarget = target;
    const result = await api("/api/site/browse?path=" + encodeURIComponent(path));
    lastBrowse = result.path;
    const section = $("#site-browser"); section.hidden = false;
    $("#site-browser-path").value = result.path;
    const list = $("#site-browser-list"); list.replaceChildren();
    list.append(button("↑ 上一级", "↑ Parent", () => act(() => browse(browserTarget, result.parent))));
    for (const entry of result.entries) {
      const b = button((entry.directory ? "▸ " : "") + entry.name, undefined, () => {
        if (entry.directory) act(() => browse(browserTarget, entry.path));
        else { $("#" + browserTarget).value = entry.path; section.hidden = true; }
      });
      b.title = entry.path; list.append(b);
    }
  }
  function changed(modelId) {
    if (modelId) {
      localStorage.setItem("cobot-capture-model", modelId);
      // The model list refreshes asynchronously; default selection follows then.
      window.dispatchEvent(new CustomEvent("cobot:model-default", {detail:modelId}));
    }
    window.CobotCollectionModel?.refresh();
    window.CobotDeploymentUI?.poll?.();
  }
  async function open() {
    if (!dialog) mount();
    dialog.showModal();
    notice(text("读取本机配置…", "Reading site configuration…"));
    await act(async () => { await refresh(); notice(text("保存配置不会启动任务。", "Saving configuration does not start a task.")); });
  }
  function mount() {
    dialog = node("dialog"); dialog.id = "site-options"; dialog.className = "site-options";
    const header = node("header"); header.className = "settings-head";
    header.append(node("h2", "启动路径与模型", "Launch paths and models"));
    const close = button("×", "×", () => dialog.close()); close.className = "icon-button panel-tool-button";
    close.setAttribute("aria-label", text("关闭", "Close")); header.append(close); dialog.append(header);
    const tabs = node("div"); tabs.className = "site-tabs"; dialog.append(tabs);
    const models = node("section"), hardware = node("section");
    models.id = "site-models"; hardware.id = "site-hardware"; hardware.hidden = true;
    for (const [id, zh, english] of [["site-models", "模型", "Models"], ["site-hardware", "机械臂 / 相机", "Arms / cameras"]]) {
      tabs.append(button(zh, english, () => { models.hidden=id!=="site-models";hardware.hidden=id!=="site-hardware"; }));
    }
    dialog.append(models, hardware);
    models.append(node("p", "路径属于运行网页的服务器。新模型需要匹配推理接口、相机顺序、动作维度、归一化和暂停 / HIL 协议。",
      "Paths belong to the web server. A new family needs matching inference, camera order, action dimensions, normalization and pause / HIL interfaces."));
    const assets = field(models, "site-assets", "已有权重", "Discovered checkpoints", "select");
    assets.addEventListener("change", selectAsset);
    const status = node("p"); status.id="site-asset-status"; models.append(status);
    field(models, "site-template", "复用适配器（仅限相同任务与格式）", "Reuse adapter (same task and format only)", "select");
    pathField(models, "site-checkpoint", "权重文件 / 完整 checkpoint 目录", "Weights / complete checkpoint directory");
    field(models, "site-label", "显示名称", "Display name");
    const compatibility = node("label"); compatibility.append(node("span", "确认任务、相机、动作顺序及归一化与所选适配器一致", "I confirm the task, cameras, action layout and normalization match this adapter"));
    const check = node("input"); check.type="checkbox";check.id="site-compatible";compatibility.prepend(check);models.append(compatibility);
    const makeDefault = node("label"); makeDefault.append(node("span", "同时设为默认选择", "Also select by default"));
    const d = node("input");d.type="checkbox";d.id="site-make-default";makeDefault.prepend(d);models.append(makeDefault);
    models.append(button("检查并登记", "Check and register", () => act(async () => {
      const result = await api("/api/site/models", {template:$("#site-template").value,
        checkpoint:$("#site-checkpoint").value.trim(), label:$("#site-label").value.trim(),
        compatible:check.checked, make_default:d.checked});
      await refresh(); changed(d.checked ? result.model_id : null);
      notice(text("已登记；尚未加载或开始推理。", "Registered; no model was loaded or started."));
    })));
    const defaults = node("div");defaults.className="site-defaults";models.append(defaults);
    field(defaults, "site-default", "默认模型（不自动运行）", "Default model (no automatic execution)", "select");
    defaults.append(button("保存默认选择", "Save default selection", () => act(async () => {
      const id = $("#site-default").value; await api("/api/site/default", {model_id:id});
      changed(id); notice(text("默认选择已保存。", "Default selection saved."));
    })));
    hardware.append(node("p", "选择前台运行的 .sh 或 ROS 1 .launch。脚本可自行准备环境；launch 需选择 setup.bash。网页设备状态和控制仍使用现有 Cobot 话题协议。",
      "Choose a foreground .sh or ROS 1 .launch. Scripts may prepare their environment; launch files require setup.bash. Device status and control still use the existing Cobot topic contract."));
    const kind=field(hardware,"site-device","设备","Device","select");
    fill(kind,[{id:"arms",label:text("机械臂","Arms")},{id:"cameras",label:text("相机","Cameras")}],"id",r=>r.label);
    kind.addEventListener("change",renderHardware);
    pathField(hardware,"site-launch","启动文件","Launch file");
    pathField(hardware,"site-setup","环境脚本（可选）","Environment setup (optional)");
    pathField(hardware,"site-cwd","工作目录（留空使用启动文件目录）","Working directory (defaults to launch directory)");
    field(hardware,"site-args","启动参数（每行一个，不写 shell 命令）","Arguments (one per line, no shell command)","textarea");
    hardware.append(button("保存启动配置","Save launch configuration",()=>act(async()=>{
      await api("/api/site/hardware",{component:kind.value,path:$("#site-launch").value.trim(),
        setup:$("#site-setup").value.trim(),cwd:$("#site-cwd").value.trim(),
        args:$("#site-args").value.split("\n").map(x=>x.trim()).filter(Boolean)});
      await refresh();notice(text("配置已保存；点击设备面板启动时使用。","Saved; used on the next explicit device start."));
    })),button("恢复内置入口","Restore built-in launcher",()=>act(async()=>{
      await api("/api/site/hardware",{component:kind.value,path:""});await refresh();
      notice(text("已恢复内置入口。","Built-in launcher restored."));
    })));
    const browser=node("section");browser.id="site-browser";browser.hidden=true;
    const bp=field(browser,"site-browser-path","浏览服务器路径","Browse server path");
    browser.append(button("打开目录","Open directory",()=>act(()=>browse(browserTarget,bp.value.trim()))),
      button("选中此目录","Select this directory",()=>{if(lastBrowse){$("#"+browserTarget).value=lastBrowse;browser.hidden=true;}}),
      button("收起浏览","Close browser",()=>{browser.hidden=true;}));
    const list=node("div");list.id="site-browser-list";browser.append(list);dialog.append(browser);
    const feedback=node("p");feedback.id="site-feedback";feedback.setAttribute("role","status");dialog.append(feedback);
    document.body.append(dialog);
  }
  function init() {
    const entry=button("启动路径与模型", "Launch paths and models", open);
    $("#settings-drawer .settings-head")?.after(entry);
    for (const selector of ["#capture-model-actions", "#deployment-facts"]) {
      const parent=$(selector); if(parent)parent.after(button("选择路径 / 登记模型","Choose path / register model",open));
    }
    window.CobotSiteOptions={open};
  }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init);else init();
})();
