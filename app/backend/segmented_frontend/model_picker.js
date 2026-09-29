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
    const parts = [];
    if (model.experiment_label) parts.push(model.experiment_label);
    if (model.family === "RLT" || model.kind === "rlt") {
      parts.push("Stage1 " + (model.stage1_step ?? model.base_step ?? "?"));
      if (model.stage !== "stage1") parts.push((model.stage || "warmup") + " " + (model.publication_tracked ? "published " + (model.published_learner_step ?? "?") : (model.learner_step ?? model.step ?? "?")));
      if (model.actor_version >= 0 && model.actor_version != null) parts.push("Actor " + model.actor_version);
    } else if (model.parent_step != null) parts.push("DAgger " + model.parent_step + " + " + (model.step ?? "?"));
    else parts.push(model.step == null ? "Steps unknown" : "Step " + model.step);
    const status = available(model) ? "available" : ({
      cli_only: "unavailable: CLI only", base_model: "base dependency",
      missing_files: "unavailable: missing files", unregistered: "unavailable: unregistered"
    }[model.availability] || "unavailable");
    return parts.join(" · ") + " · " + status;
  }
  function renderDetails(container, model) {
    if (!container) return;
    const rows = model ? [
      ["Model", familyOf(model)], ["Task", taskOf(model)], ["Base model", model.base_checkpoint],
      ["Training lineage", model.training_lineage], ["Launch command", model.cli_command], ["Stage1 checkpoint", model.stage1_step],
      ["Learner trained step", model.learner_step],
      ["Learner internal Actor", model.publication_tracked ? model.learner_actor_version : null],
      ["Published learner step", model.publication_tracked ? (model.published_learner_step ?? "Unknown") : null],
      ["Published Actor", model.publication_tracked ? (model.published_actor_version ?? "Unknown") : null],
      ["Last inference Actor", model.publication_tracked ? (model.last_inference_actor_version ?? "Not observed") : null],
      ["Inference episode", model.publication_tracked ? model.inference_episode_id : null],
      ["Publication status", model.publication_tracked ? (model.published_learner_step == null ? "Publication not verified" : model.learner_step > model.published_learner_step ? (model.learner_step - model.published_learner_step) + " trained steps not yet published" : "Published snapshot is up to date") : null],
      ["Actor version", model.publication_tracked ? null : model.actor_version],
      ["Training steps", model.publication_tracked ? null : model.step], ["Control rate", model.control_hz ? model.control_hz + " Hz" : null],
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
    container.replaceChildren(sceneField, familyField, modelField, path, hint);
    let models = [], selected = "", scene = "", family = "", initialized = false, disabled = false, locked = false;
    let sceneSignature = "", modelSignature = "", familySignature = "";
    const current = () => models.find(model => model.id === selected);
    function render() {
      setText(sceneTitle, text("场景", "Scene"));
      setText(familyTitle, text("模型", "Model"));
      setText(modelTitle, text("步数", "Steps"));
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
      const matching = sceneModels.filter(model => familyOf(model) === family);
      const labels = matching.map(stepLabel);
      const nextModelSignature = JSON.stringify([english(), matching, labels]);
      if (modelSignature !== nextModelSignature) {
        modelSignature = nextModelSignature;
        const placeholder = node("option", !models.length ? "Reading models…"
          : matching.some(available) ? "Choose steps" : "No available checkpoints");
        placeholder.value = "";
        placeholder.disabled = true;
        modelSelect.replaceChildren(placeholder, ...matching.map((model, index) => {
          let title = labels[index];
          if (labels.indexOf(title) !== labels.lastIndexOf(title)) {
            title += " · " + (model.checkpoint?.split("/").slice(-3).join("/") || model.id);
          }
          const option = node("option", title);
          option.value = model.id;
          option.disabled = !available(model);
          option.title = [model.checkpoint, reason(model)].filter(Boolean).join("\n");
          return option;
        }));
      }
      if (modelSelect.value !== selected) modelSelect.value = selected;
      const chosen = current();
      const pathText = chosen?.checkpoint || "";
      setText(path, pathText ? text("权重路径：", "Weights: ") + pathText : "");
      path.title = pathText;
      path.hidden = !pathText;
      setText(hint, chosen && !available(chosen) ? reason(chosen)
        : models.length && !matching.some(available) ? text("当前场景的模型暂不可用，请查看灰色选项的原因或切换场景。", "Models in this scene are unavailable. Check the disabled options or choose another scene.") : "");
      hint.hidden = !hint.textContent;
      sceneSelect.disabled = disabled;
      familySelect.disabled = disabled;
      modelSelect.disabled = disabled;
    }
    function notify(persist, source) {
      if (persist) {
        root.localStorage.setItem(storageKey, JSON.stringify({scene, family, model: selected}));
        if (selected) {
          root.localStorage.setItem("cobot-capture-model", selected);
          root.localStorage.setItem("cobot-collection-model-id", selected);
        }
      }
      onChange({modelId: selected, scene, model: current(), source});
      if (persist) root.dispatchEvent(new CustomEvent(eventName, {detail: {owner: sceneId, scene, family, model: selected}}));
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
    modelSelect.addEventListener("change", () => {
      const chosen = models.find(model => model.id === modelSelect.value);
      if (disabled || !available(chosen)) { render(); return; }
      selected = chosen.id;
      scene = taskOf(chosen); family = familyOf(chosen);
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
      render();
      notify(false, "peer");
    });
    document.addEventListener("cobot:language", render);
    return {
      update(list, preferred) {
        models = list || [];
        if (!initialized && models.length) {
          const saved = savedSelection();
          const preferredModel = models.find(model => model.id === (saved?.model || preferred) && available(model));
          scene = preferredModel ? taskOf(preferredModel) : saved?.scene || "";
          if (scene && !models.some(model => taskOf(model) === scene)) scene = "";
          const fallback = models.find(model => available(model) && (!scene || taskOf(model) === scene));
          selected = preferredModel?.id || fallback?.id || "";
          if (selected) { scene = taskOf(current()); family = familyOf(current()); }
          else family = saved?.family || "";
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
          scene = taskOf(chosen); family = familyOf(chosen);
        }
        render();
        return selected;
      },
      setDisabled(value, {lockSelection = value} = {}) { disabled = Boolean(value); locked = Boolean(lockSelection); render(); },
      get value() { return selected; },
      get scene() { return scene; }
    };
  }
  root.CobotModelPicker = {create, available, sceneLabel, stepLabel, renderDetails};
})(window);
