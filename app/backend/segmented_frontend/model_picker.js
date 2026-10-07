"use strict";
(function(root) {
  const storageKey = "cobot-model-selection";
  const eventName = "cobot:model-selection";
  const english = () => root.CobotPreferences?.language === "en";
  const text = (zh, en) => english() ? en : zh;
  const taskOf = model => String(model?.task || "unspecified");
  const available = model => Boolean(model?.available) && model?.capabilities?.load !== false;
  const familyOf = model => String(model?.family || model?.kind || "Unregistered");
  const sceneLabel = task => task;
  function stepLabel(model) {
    return String((model.kind === "rlt" || model.family === "RLT") ? (model.stage === "stage1" ? model.stage1_step ?? model.base_step ?? model.step ?? "?" : model.publication_tracked ? model.published_learner_step ?? "?" : model.learner_step ?? model.step ?? "?") : model.step ?? "?");
  }
  const methodOf=model=>String(model?.training_method || ((model.kind==='rlt'||model.family==='RLT')?(model.runtime_profile==='credit_mc30'?'mc30':'original'):model.parent_step!=null?'dagger':'original'));
  const methodLabel=method=>method==='original'?text('原版','Original'):method==='mc30'?text('MC30 优化','MC30 optimized'):method==='dagger'?'DAgger':method;
  const visible=model=>/^\d+$/.test(stepLabel(model))&&model?.capabilities?.load!==false&&!['unregistered','base_model','cli_only'].includes(model.availability)&&!((model.kind==='rlt'||model.family==='RLT')&&['stage1','reference'].includes(model.stage||model.mode));
  const isRlt = model => model?.kind === "rlt" || model?.family === "RLT";
  function modeOf(model) {
    if (!isRlt(model)) return "";
    if (model.training_enabled === false || model.mode === "frozen" || model.stage === "frozen") return "frozen";
    if (model.training_enabled === true || model.mode === "online" || model.stage === "online") return "online";
    return "unknown";
  }
  function modeLabel(model) {
    return ({frozen:text("冻结", "Frozen"), online:"Online", unknown:text("模式待核验", "Mode unverified")})[modeOf(model)] || "";
  }
  function optionLabel(model) {
    return [stepLabel(model), modeLabel(model), isRlt(model) && model.actor_version != null ? "Actor " + model.actor_version : ""].filter(Boolean).join(" · ");
  }
  function runtimeIdentity(state) {
    const model = state?.model;
    if (!isRlt(model)) return "";
    const session = state.session || {};
    const frozen = session.evaluation_only === true || modeOf(model) === "frozen";
    const enabled = model.training_enabled;
    const learner = session.learner_version;
    const actor = session.actor_version ?? model.last_inference_actor_version;
    return [text("实际加载：", "Loaded: ") + (frozen ? text("冻结", "Frozen") : modeLabel(model)),
      frozen || enabled === false ? text("Learner 关闭", "Learner disabled") : enabled === true ? text("Learner 已启用", "Learner enabled") : text("Learner 状态待核验", "Learner state unverified"),
      learner != null ? "Learner " + learner : model.learner_step != null ? text("登记 Learner ", "Registered Learner ") + model.learner_step : "",
      actor != null ? text("最近推理 Actor ", "Last inference Actor ") + actor : model.actor_version != null ? text("登记 Actor ", "Registered Actor ") + model.actor_version + text("（推理未观测）", " (inference not observed)") : text("执行 Actor 待核验", "Inference Actor unverified"),
      model.id].filter(Boolean).join(" · ");
  }
  function weightKey(model) {
    return JSON.stringify([familyOf(model),taskOf(model),model.checkpoint||model.id,methodOf(model),modeOf(model)]);
  }

  function renderDetails(container, model) {
    if (!container) return;
    const rows = model ? [
      ["Model", familyOf(model)], ["Model ID", model.id], ["Mode", modeLabel(model)], ["Learner enabled (reported)", model.training_enabled == null ? null : String(model.training_enabled)], ["Method", methodLabel(methodOf(model))], ["Task", taskOf(model)],
      ["Training lineage", model.training_lineage], ["Training origin step", model.source_step], ["Launch command", model.cli_command], ["Stage1 checkpoint", model.stage1_step],
      ["Learner trained step", model.learner_step],
      ["Learner internal Actor", model.publication_tracked ? model.learner_actor_version : null],
      ["Published learner step", model.publication_tracked ? (model.published_learner_step ?? "Unknown") : null],
      ["Published Actor", model.publication_tracked ? (model.published_actor_version ?? "Unknown") : null],
      ["Last inference Actor", model.publication_tracked ? (model.last_inference_actor_version ?? "Not observed") : null],
      ["Inference episode", model.publication_tracked ? model.inference_episode_id : null],
      ["Publication status", model.publication_tracked ? (model.published_learner_step == null ? "Publication not verified" : model.learner_step > model.published_learner_step ? (model.learner_step - model.published_learner_step) + " trained steps not yet published" : "Published snapshot is up to date") : null],
      ["Actor version", model.publication_tracked ? null : model.actor_version],
      ["Training steps", model.publication_tracked ? null : model.step],
      ["Action publication rate", (model.execution_settings?.publish_hz ?? model.publish_hz ?? model.control_hz) ? (model.execution_settings?.publish_hz ?? model.publish_hz ?? model.control_hz) + " Hz" : null],
      ["Active action publication rate", model.active_execution_settings ? model.active_execution_settings.publish_hz + " Hz" : null],
      ["Active RTC", model.active_execution_settings ? (model.active_execution_settings.rtc ? "On" : "Off") : null],
      ["Active causal smoothing", model.active_execution_settings ? (model.active_execution_settings.smoothing ? "On" : "Off") : null],
      ["Logical control / Replay rate", (model.kind === "rlt" || model.family === "RLT") ? (model.execution_settings?.logical_hz ?? model.control_hz ?? 20) + " Hz" : null],
      ["RTC", model.execution_settings ? (model.execution_settings.rtc ? "On" : "Off") : null],
      ["Causal smoothing", model.execution_settings ? (model.execution_settings.smoothing ? "On" : "Off") : null],
      ["Action mode", model.deterministic === true ? "Deterministic / no exploration" : model.kind === "pi05" ? "Original RTC inference" : null],
      ["Validation", model.validation], ["Availability", available(model) ? "Available" : reason(model)]
    ].filter(([,v]) => v != null && v !== "") : [];
    const signature = JSON.stringify(rows);
    if (container.dataset.signature === signature) return;
    container.dataset.signature = signature;
    container.replaceChildren(...rows.flatMap(([k,v]) => [node("dt",k),node("dd",String(v))]));
  }
  function savedSelection() {
    try { return JSON.parse(root.localStorage.getItem(storageKey) || "null"); }
    catch (_) { return null; }
  }
  function reason(model) {
    return model.unavailable_reason_en || (available(model) ? "" : "Unavailable in the web");
  }
  function setText(element, value) { if (element.textContent !== value) element.textContent = value; }
  function node(tag, value, className) {
    const element = document.createElement(tag);
    if (value != null) element.textContent = value;
    if (className) element.className = className;
    return element;
  }
  function create({container, modelSelect, sceneId, onChange = () => {}}) {
    const sceneSelect = node("select");
    sceneSelect.id = sceneId;
    const familySelect = node("select"); familySelect.id = modelSelect.id + "-family";
    const familyTitle = node("span"), familyField = node("label");
    familyField.append(familyTitle, familySelect);
    const methodSelect=node("select"),methodTitle=node("span"),methodField=node("label");
    methodSelect.id=modelSelect.id+"-method";methodField.append(methodTitle,methodSelect);
    const sceneTitle = node("span"), modelTitle = node("span");
    const sceneField = node("label"), modelField = node("label");
    sceneField.append(sceneTitle, sceneSelect);
    modelField.append(modelTitle, modelSelect);
    const path = node("code", "", "model-selection-path");
    path.id = modelSelect.id + "-path";
    const hint = node("p", "", "model-selection-hint");
    hint.id = modelSelect.id + "-hint";
    hint.setAttribute("role", "status");
    modelSelect.setAttribute("aria-describedby", path.id + " " + hint.id);
    container.classList.add("model-picker-pair");
    container.replaceChildren(sceneField, familyField, methodField, modelField, path, hint);
    let models = [], selected = "", scene = "", family = "", method = "", initialized = false, disabled = false, locked = false;
    let sceneSignature = "", modelSignature = "", familySignature = "", methodSignature = "";
    const current = () => models.find(model => model.id === selected);
    function render() {
      setText(sceneTitle, text("场景", "Scene"));
      setText(familyTitle, text("模型", "Model"));
      setText(modelTitle, text("版本／模式", "Version / mode"));
      setText(methodTitle,text("方法","Method"));methodSelect.setAttribute("aria-label",methodTitle.textContent);
      familySelect.setAttribute("aria-label", familyTitle.textContent);
      sceneSelect.setAttribute("aria-label", sceneTitle.textContent);
      modelSelect.setAttribute("aria-label", modelTitle.textContent);
      const tasks = [...new Set(models.map(taskOf))];
      const nextSceneSignature = JSON.stringify([english(), tasks]);
      if (sceneSignature !== nextSceneSignature) {
        sceneSignature = nextSceneSignature;
        sceneSelect.replaceChildren(...["", ...tasks].map(task => {
          const option = node("option", task ? sceneLabel(task) : "All scenes");
          option.value = task;
          return option;
        }));
      }
      if (sceneSelect.value !== scene) sceneSelect.value = scene;
      const sceneModels = models.filter(model => !scene || taskOf(model) === scene);
      const families = [...new Set(sceneModels.map(familyOf))];
      if (!families.includes(family)) family = families[0] || "";
      const nextFamilySignature = JSON.stringify(families);
      if (familySignature !== nextFamilySignature) {
        familySignature = nextFamilySignature;
        familySelect.replaceChildren(...families.map(value => {
          const option = node("option", value); option.value = value; return option;
        }));
      }
      familySelect.value = family;
      const familyModels = sceneModels.filter(model => familyOf(model) === family);
      const methods=[...new Set(familyModels.map(methodOf))];
      if(!methods.includes(method))method=methods[0]||'';
      const nextMethodSignature=JSON.stringify([english(),methods]);
      if(methodSignature!==nextMethodSignature){methodSignature=nextMethodSignature;methodSelect.replaceChildren(...methods.map(value=>{const option=node('option',methodLabel(value));option.value=value;return option;}));}
      methodSelect.value=method;
      const variants = familyModels.filter(model=>methodOf(model)===method);
      const candidates = variants.filter(model => model === (variants.find(other => weightKey(other) === weightKey(model) && other.id === selected) || variants.find(other => weightKey(other) === weightKey(model) && other.training_enabled && !other.execution_profile) || variants.find(other => weightKey(other) === weightKey(model) && !other.execution_profile) || variants.find(other => weightKey(other) === weightKey(model))));
      const matching = candidates.sort((a,b)=>Number(stepLabel(a))-Number(stepLabel(b)));
      const labels = matching.map(optionLabel);
      const nextModelSignature = JSON.stringify([english(), matching, labels]);
      if (modelSignature !== nextModelSignature) {
        modelSignature = nextModelSignature;
        const placeholder = node("option", !models.length ? "Reading models…"
          : matching.some(available) ? "Choose steps" : "No available checkpoints");
        placeholder.value = "";
        placeholder.disabled = true;
        modelSelect.replaceChildren(placeholder, ...matching.map((model, index) => {
          const option = node("option", labels[index]);
          option.value = model.id;
          option.disabled = !available(model);
          option.title = [model.checkpoint, reason(model)].filter(Boolean).join("\n");
          return option;
        }));
      }
      if (modelSelect.value !== selected) modelSelect.value = selected;
      const chosen = current();
      const pathText = [
        chosen?.checkpoint ? text("权重路径：", "Weights: ") + chosen.checkpoint : "",
        chosen?.base_checkpoint ? "Base model: " + chosen.base_checkpoint : ""
      ].filter(Boolean).join("\n");
      setText(path, pathText);
      path.title = pathText;
      path.hidden = !pathText;
      setText(hint, chosen && !available(chosen) ? reason(chosen)
        : chosen ? [text("选中：", "Selected: ") + modeLabel(chosen), chosen.label || "", chosen.id, chosen.learner_step != null ? text("登记 Learner ", "Registered Learner ") + chosen.learner_step : ""].filter(Boolean).join(" · ") : models.length && !matching.some(available) ? text("当前场景的模型暂不可用，请查看灰色选项的原因或切换场景。", "Models in this scene are unavailable. Check the disabled options or choose another scene.") : "");
      hint.hidden = !hint.textContent;
      sceneSelect.disabled = disabled;
      familySelect.disabled = disabled;
      methodSelect.disabled = disabled;
      modelSelect.disabled = disabled;
    }
    function notify(persist, source) {
      if (persist) {
        root.localStorage.setItem(storageKey, JSON.stringify({scene, family, method, model: selected}));
        if (selected) {
          root.localStorage.setItem("cobot-capture-model", selected);
          root.localStorage.setItem("cobot-collection-model-id", selected);
        }
      }
      onChange({modelId: selected, scene, model: current(), source});
      if (persist) root.dispatchEvent(new CustomEvent(eventName, {detail: {owner: sceneId, scene, family, method, model: selected}}));
    }
    sceneSelect.addEventListener("change", () => {
      if (disabled) { render(); return; }
      scene = sceneSelect.value;
      if (!available(current()) || (scene && taskOf(current()) !== scene)) selected = "";
      render();
      notify(true, "user");
    });
    familySelect.addEventListener("change", () => {
      if (disabled) { render(); return; }
      family = familySelect.value;
      if (!available(current()) || familyOf(current()) !== family) selected = "";
      render(); notify(true, "user");
    });
    methodSelect.addEventListener('change',()=>{
      if(disabled){render();return;}method=methodSelect.value;
      if(methodOf(current())!==method)selected='';render();notify(true,'user');
    });
    modelSelect.addEventListener("change", () => {
      const chosen = models.find(model => model.id === modelSelect.value);
      if (disabled || !available(chosen)) { render(); return; }
      selected = chosen.id;
      scene = taskOf(chosen); family = familyOf(chosen); method=methodOf(chosen);
      render();
      notify(true, "user");
    });
    root.addEventListener(eventName, event => {
      const selection = event.detail;
      if (!initialized || locked || selection.owner === sceneId) return;
      const chosen = models.find(model => model.id === selection.model);
      if (chosen && !available(chosen)) return;
      if (selection.model && !chosen) return;
      if (selection.scene && !models.some(model => taskOf(model) === selection.scene)) return;
      selected = chosen?.id || "";
      scene = chosen ? taskOf(chosen) : selection.scene || "";
      family = chosen ? familyOf(chosen) : selection.family || "";
      method=chosen?methodOf(chosen):selection.method||"";
      render();
      notify(false, "peer");
    });
    document.addEventListener("cobot:language", render);
    return {
      update(list, preferred) {
        models = (list || []).filter(visible);
        if (!initialized && models.length) {
          const saved = savedSelection();
          const preferredModel = models.find(model => model.id === (saved?.model || preferred) && available(model));
          scene = preferredModel ? taskOf(preferredModel) : saved?.scene || "";
          if (scene && !models.some(model => taskOf(model) === scene)) scene = "";
          const fallback = models.find(model => available(model) && (!scene || taskOf(model) === scene));
          selected = preferredModel?.id || fallback?.id || "";
          if (selected) { scene = taskOf(current()); family = familyOf(current()); method=methodOf(current()); }
          else {family = saved?.family || "";method=saved?.method||"";}
          initialized = true;
        }
        if (selected && !models.some(model => model.id === selected)) selected = "";
        if (scene && !models.some(model => taskOf(model) === scene)) scene = "";
        render();
        return selected;
      },
      select(id, {loaded = false} = {}) {
        const chosen = models.find(model => model.id === id);
        if (chosen && (loaded || available(chosen))) {
          selected = chosen.id;
          scene = taskOf(chosen); family = familyOf(chosen); method=methodOf(chosen);
        }
        render();
        return selected;
      },
      setDisabled(value, {lockSelection = value} = {}) { disabled = Boolean(value); locked = Boolean(lockSelection); render(); },
      get value() { return selected; },
      get scene() { return scene; }
    };
  }
  root.CobotModelPicker = {create, available, methodOf, visible, sceneLabel, stepLabel, optionLabel, modeOf, modeLabel, runtimeIdentity, renderDetails};
})(window);
