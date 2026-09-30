"""Read model-owned execution contracts; never implement an executor here."""
import runpy
from .paths import RLT, PROJECT

def contract():
    return runpy.run_path(str(PROJECT.parent / 'vla-platform/integrations/cobot/execution_options.py'))

def configured_model(model, options=None):
    result = dict(model)
    if options is not None:
        options = contract()['normalize_options'](options)
        if model.get('kind') == 'external' and options.get('enabled'):
            raise ValueError('external_adapter_requires_execution_contract')
        result['execution_options'] = contract()['normalize_options'](options)
    if result.get('kind') == 'rlt':
        result['execution_settings'] = runpy.run_path(str(RLT / 'methods/openpi_rlt/cobot_adapter/execution_profiles.py'))['describe_execution'](result, RLT)
    else:
        result['execution_settings'] = contract()['describe_execution'](result)
    return result
