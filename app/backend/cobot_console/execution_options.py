"""Read model-owned execution contracts; never implement an executor here."""
import runpy
from .paths import RLT, PROJECT


def shared_contract():
    return runpy.run_path(str(PROJECT.parent / 'vla-platform/integrations/cobot/execution_options.py'))


def normalize_options(options):
    # Route-level parsing accepts the native EXPO rate. Model-level validation
    # below still rejects 10Hz for adapters whose contracts only allow 20..50.
    if isinstance(options, dict) and type(options.get('publish_hz')) is int and options['publish_hz'] == 10:
        checked = shared_contract()['normalize_options']({**options, 'publish_hz': 20})
        return {**checked, 'publish_hz': 10}
    return shared_contract()['normalize_options'](options)


def contract():
    return {**shared_contract(), 'normalize_options': normalize_options}


def configured_model(model, options=None):
    result = dict(model)
    expo = model.get('runtime_adapter') == 'expo_left'
    if options is not None:
        validate = normalize_options if expo else shared_contract()['normalize_options']
        options = validate(options)
        if model.get('kind') == 'external' and options.get('enabled'):
            raise ValueError('external_adapter_requires_execution_contract')
        result['execution_options'] = options
    if expo:
        selected = normalize_options(result.get('execution_options', {'enabled': False}))
        defaults = dict(enabled=False, publish_hz=10, logical_hz=10, rtc=True, smoothing=False)
        result['execution_settings'] = {**defaults, **selected} if selected['enabled'] else defaults
    elif result.get('kind') == 'rlt':
        result['execution_settings'] = runpy.run_path(str(RLT / 'methods/openpi_rlt/cobot_adapter/execution_profiles.py'))['describe_execution'](result, RLT)
    else:
        result['execution_settings'] = shared_contract()['describe_execution'](result)
    return result
