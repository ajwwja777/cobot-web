"""Read the model-owned execution contract; never implement an executor here."""
import runpy
from .paths import RLT

def contract():
    return runpy.run_path(str(RLT / 'methods/openpi_rlt/cobot_adapter/execution_profiles.py'))

def configured_model(model, options=None):
    result = dict(model)
    if options is not None:
        if model.get('kind') != 'rlt':
            raise ValueError('execution_options_not_supported_for_this_adapter')
        result['execution_options'] = contract()['normalize_options'](options)
    if result.get('kind') == 'rlt':
        result['execution_settings'] = contract()['describe_execution'](result, RLT)
    return result
