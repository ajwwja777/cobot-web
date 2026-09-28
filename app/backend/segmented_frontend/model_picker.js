"use strict";
(function(root) {
  const storageKey = "cobot-model-selection";
  const eventName = "cobot:model-selection";
  const english = () => root.CobotPreferences?.language === "en";
  const text = (zh, en) => english() ? en : zh;
  const taskOf = model => String(model?.task || "unspecified");
  const available = model => Boolean(model?.available) && model?.capabilities?.load !== false;
  const scenes = {
    plug_insertion: ["插孔", "Plug insertion"],
    in_the_pot: ["放入锅中", "In the pot"],
    lift_book: ["抬书", "Lift book"],
    put_two_fruits: ["放置两种水果", "Put two fruits"],
    base: ["基础模型", "Base models"],
    unspecified: ["未指定场景", "Unspecified scene"]
  };
  function sceneLabel(task) {
    const name = scenes[task];
    return name ? text(...name) + " · " + task : task;
  }
  function savedSelection() {
    try { return JSON.parse(root.localStorage.getItem(storageKey) || "null"); }
    catch (_) { return null; }
  }
  function reason(model) {
    return english()
      ? model.unavailable_reason_en || (available(model) ? "" : "Unavailable in the web")
      : model.unavailable_reason || (available(model) ? "" : "网页不可用");
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
    container.replaceChildren(sceneField, modelField, path, hint);
    let models = [], selected = "", scene = "", initialized = false, disabled = false, locked = false;
    let sceneSignature = "", modelSignature = "";
    const current = () => models.find(model => model.id === selected);
    function render() {
      setText(sceneTitle, text("场景", "Scene"));
      setText(modelTitle, text("模型", "Model"));
      sceneSelect.setAttribute("aria-label", sceneTitle.textContent);
      modelSelect.setAttribute("aria-label", modelTitle.textContent);
      const tasks = [...new Set(models.map(taskOf))];
      const nextSceneSignature = JSON.stringify([english(), tasks]);
      if (sceneSignature !== nextSceneSignature) {
        sceneSignature = nextSceneSignature;
        sceneSelect.replaceChildren(...["", ...tasks].map(task => {
          const option = node("option", task ? sceneLabel(task) : text("全部场景", "All scenes"));
          option.value = task;
          return option;
        }));
      }
      if (sceneSelect.value !== scene) sceneSelect.value = scene;
      const matching = models.filter(model => !scene || taskOf(model) === scene);
      const labels = matching.map(model => root.CobotModelChoiceLabel(model, {compact: true}));
      const nextModelSignature = JSON.stringify([english(), matching, labels]);
      if (modelSignature !== nextModelSignature) {
        modelSignature = nextModelSignature;
        const placeholder = node("option", !models.length ? text("正在读取模型…", "Reading models…")
          : matching.some(available) ? text("选择模型", "Choose a model") : text("该场景暂无可加载模型", "No loadable models in this scene"));
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
      modelSelect.disabled = disabled;
    }
    function notify(persist, source) {
      if (persist) {
        root.localStorage.setItem(storageKey, JSON.stringify({scene, model: selected}));
        if (selected) {
          root.localStorage.setItem("cobot-capture-model", selected);
          root.localStorage.setItem("cobot-collection-model-id", selected);
        }
      }
      onChange({modelId: selected, scene, model: current(), source});
      if (persist) root.dispatchEvent(new CustomEvent(eventName, {detail: {owner: sceneId, scene, model: selected}}));
    }
    sceneSelect.addEventListener("change", () => {
      if (disabled) { render(); return; }
      scene = sceneSelect.value;
      if (!available(current()) || (scene && taskOf(current()) !== scene)) selected = "";
      render();
      notify(true, "user");
    });
    modelSelect.addEventListener("change", () => {
      const chosen = models.find(model => model.id === modelSelect.value);
      if (disabled || !available(chosen)) { render(); return; }
      selected = chosen.id;
      scene = taskOf(chosen);
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
          if (selected) scene = taskOf(models.find(model => model.id === selected));
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
          scene = taskOf(chosen);
        }
        render();
        return selected;
      },
      setDisabled(value, {lockSelection = value} = {}) { disabled = Boolean(value); locked = Boolean(lockSelection); render(); },
      get value() { return selected; },
      get scene() { return scene; }
    };
  }
  root.CobotModelPicker = {create, available, sceneLabel};
})(window);
