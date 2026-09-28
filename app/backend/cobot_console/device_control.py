"""Compatibility facade. Hardware rules are maintained in cobot-control."""
from .control_import import control_package
control_package()
from cobot_control import device_control as hardware
from cobot_control.device_control import DeviceControlError
from .paths import PROJECT as PLATFORM, RUNTIME_ROOT, SETTINGS, CONTROL, POSE_CONFIG
SCRIPTS = PLATFORM / "scripts"
_TARGETS = dict(hardware._TARGETS, rlt={"online", "frozen", "reference", "warmup"},
                rlt_model={"plug_v3-stage1-reference", "plug_v3-frozen-latest", "plug_v3-online-latest"})
_STOP_MARKERS = dict(hardware._STOP_MARKERS, rlt="methods.openpi_rlt.scripts.online_role")
# Keep historical read-only import paths working.
for _name in dir(hardware):
    if not _name.startswith("__") and _name not in globals():
        globals()[_name] = getattr(hardware, _name)

class DeviceController(hardware.DeviceController):
    components = hardware.DeviceController.components | {"rlt", "console"}
    stop_markers = _STOP_MARKERS

    def __init__(self, runtime, *, extra_runtime=None, **kwargs):
        super().__init__(runtime, **kwargs)
        self.extra_runtime = Path(extra_runtime or runtime)
        self.extra_runtime.mkdir(parents=True, exist_ok=True)

    @property
    def job_directories(self):
        return list(dict.fromkeys([self.runtime, self.extra_runtime]))

    def _directory(self, component):
        return self.extra_runtime if component in {"rlt", "console"} else self.runtime

    def _normalize(self, spec):
        if not isinstance(spec, dict) or spec.get("component") not in {"rlt", "console"}:
            return super()._normalize(spec)
        component, action = spec.get("component"), spec.get("action")
        allowed_keys = {"component", "action"}
        result = {"component": component, "action": action}
        if component == 'rlt' and action == 'start' and spec.get('target') in _TARGETS['rlt']:
            allowed_keys.add('target')
            result['target'] = str(spec['target'])
            if spec.get('model') is not None:
                model = str(spec['model'])
                if model not in _TARGETS['rlt_model']:
                    raise DeviceControlError('model is not registered')
                if model == 'plug_v3-stage1-reference' and result['target'] not in ('reference','warmup'):
                    raise DeviceControlError('Stage-1 reference must use reference or warmup mode')
                if model == 'plug_v3-frozen-latest' and result['target'] != 'frozen':
                    raise DeviceControlError('frozen actor must use frozen mode')
                if model == 'plug_v3-online-latest' and result['target'] != 'online':
                    raise DeviceControlError('online actor must use online mode')
                allowed_keys.add('model')
                result['model'] = model
        elif component == 'rlt' and action in ('stop','down'):
            if spec.get('model') is not None:
                model = str(spec['model'])
                if model not in _TARGETS['rlt_model']:
                    raise DeviceControlError('model is not registered')
                allowed_keys.add('model')
                result['model'] = model
        elif component == 'console' and action == 'stop':
            pass
        else:
            raise DeviceControlError("unsupported device operation")
        if set(spec) != allowed_keys:
            raise DeviceControlError("unsupported device arguments")
        return result

    def _command(self, spec):
        component, action = spec["component"], spec["action"]
        if component not in {"rlt", "console"}:
            return super()._command(spec)
        if component == 'console':
            return [str(SCRIPTS/'ui_shutdown_after_response.sh')]
        model = spec.get('model','')
        if action == 'stop':
            return [str(SCRIPTS/'rlt_stop.sh')]
        if action == 'down':
            return [str(SCRIPTS/('rlt_v3_down.sh' if model.startswith('plug_v3-') else 'rlt_down.sh'))]
        if model.startswith('plug_v3-'):
            mode = spec['target']
            return [str(SCRIPTS/'rlt_v3_up.sh'),mode]
        if spec['target'] == 'frozen':
            return [str(SCRIPTS/'rlt_demo.sh')]
        if spec['target'] == 'reference':
            return [str(SCRIPTS/'rlt_up.sh'),'--reference','--no-record']
        return [str(SCRIPTS/'rlt_up.sh')]

    def _managed_stop_available(self, component):
        return super()._managed_stop_available(component) and (component != "rlt" or bool(self.process_finder(self.stop_markers["rlt"])))

    def _before_start(self, spec):
        component, action = spec["component"], spec["action"]
        if component == 'rlt' and action == 'start':
            existing = self.process_finder(_STOP_MARKERS['rlt'])
            if existing:
                raise DeviceControlError(
                    'RLT backend already running; reuse it or stop it before starting another model'
                )
