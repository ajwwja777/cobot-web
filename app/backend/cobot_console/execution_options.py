"""Read model-owned execution contracts; never implement an executor here."""
import runpy
from .paths import RLT, PROJECT


def shared_contract():
    return runpy.run_path(str(PROJECT.parent / 'vla-platform/integrations/cobot/execution_options.py'))


def expo_contract():
    return runpy.run_path(str(RLT / 'methods/expo_ft/cobot_adapter/online_options.py'))


def normalize_options(options):
    # Shared Hz/RTC controls remain strict; EXPO-owned options are validated
    # independently and accepted only for models advertising that capability.
    base = dict(options) if isinstance(options, dict) else options
    expo = base.pop('expo', None) if isinstance(base, dict) else None
    if isinstance(options, dict) and 'expo' in options:
        expo = expo_contract()['normalize'](expo)
        if not base.get('enabled'):
            raise ValueError('EXPO choices require enabled execution options')
    native10 = isinstance(base, dict) and type(base.get('publish_hz')) is int and base['publish_hz'] == 10
    checked = shared_contract()['normalize_options']({**base, 'publish_hz': 20} if native10 else base)
    if native10: checked['publish_hz'] = 10
    if expo is not None: checked['expo'] = expo
    return checked


def contract():
    return {**shared_contract(), 'normalize_options': normalize_options}


def configured_model(model, options=None):
    result = dict(model)
    expo = model.get('runtime_adapter') in {'expo_left', 'expo_online'}
    if options is not None:
        validate = normalize_options if expo else shared_contract()['normalize_options']
        options = validate(options)
        if model.get('kind') == 'external' and options.get('enabled'):
            raise ValueError('external_adapter_requires_execution_contract')
        if 'expo' in options and not (model.get('runtime_adapter') == 'expo_online' and model.get('expo_online_options')):
            raise ValueError('This model does not support configurable EXPO Online parameters')
        result['execution_options'] = options
    if expo:
        selected = normalize_options(result.get('execution_options', {'enabled': False}))
        defaults = dict(enabled=False, publish_hz=10, logical_hz=10, rtc=True, smoothing=False)
        result['execution_settings'] = {**defaults, **selected} if selected['enabled'] else defaults
    elif result.get('kind') == 'rlt':
        result['execution_settings'] = runpy.run_path(str(RLT / 'methods/openpi_rlt/cobot_adapter/execution_profiles.py'))['describe_execution'](result, RLT)
    else:
        result['execution_settings'] = shared_contract()['describe_execution'](result)
    if model.get('expo_online_options'):
        chosen = result.get('execution_options', {}).get('expo', model['expo_online_options']['defaults'])
        result['execution_settings']['expo'] = expo_contract()['normalize'](chosen)
    if model.get('runtime_adapter') == 'expo_online' and not result['execution_settings']['rtc']:
        raise ValueError('EXPO 在线学习需要开启 RTC，以匹配训练的延迟输入')
    return result
