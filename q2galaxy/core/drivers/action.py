# ----------------------------------------------------------------------------
# Copyright (c) 2018-2021, QIIME 2 development team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file LICENSE, distributed with this software.
# ----------------------------------------------------------------------------
import sys

import qiime2
import qiime2.sdk as sdk

from q2galaxy.core.util import get_mystery_stew
from q2galaxy.core.drivers.stdio import (
    error_handler, stdio_files, GALAXY_TRIMMED_STRING_LEN)


def action_runner(plugin_id, action_id, inputs):
    # Each helper below is decorated to accept stdout and stderr, the goal is
    # to catch issues and promote the error message to the start of stdout and
    # stderr so that Galaxy's misc_info block will be the most relevant info.
    # Otherwise, you tend to end up with a traceback or the start of stdout
    # for noisy actions. To preserve stdout and stderr, we do want to log them
    # and then emit them at the end after writing out the relevant error first
    with stdio_files() as stdio:
        action = _get_action(plugin_id, action_id,
                             _stdio=stdio)
        action_kwargs = _convert_arguments(action.signature, inputs,
                                           _stdio=stdio)
        results = _execute_action(action, action_kwargs,
                                  _stdio=stdio)
        _save_results(results,
                      _stdio=stdio)


def get_version(plugin_id):
    plugin = _get_plugin(plugin_id)
    return plugin.version


def _get_plugin(plugin_id):
    if plugin_id == 'mystery_stew':
        return get_mystery_stew()
    else:
        pm = sdk.PluginManager()
        return pm.get_plugin(id=plugin_id)


@error_handler(header="Unexpected error finding the action in q2galaxy: ")
def _get_action(plugin_id, action_id):
    plugin = _get_plugin(plugin_id)
    action = plugin.actions[action_id]

    return action


@error_handler(header="Unexpected error loading arguments in q2galaxy: ")
def _convert_arguments(signature, inputs):
    processed_inputs = {}

    all_inputs_params = {}
    all_inputs_params.update(signature.parameters)
    all_inputs_params.update(signature.inputs)
    for k, v in inputs.items():
        try:
            type_ = all_inputs_params[k].qiime_type
        # If we had a metadata column arg then the extra arg generated to
        # accept the column specifier won't be in the signature and should be
        # skipped
        except KeyError as e:
            if '_source_data' in str(e):
                continue
            else:
                raise(e)

        if v is None:
            processed_inputs[k] = None

        elif qiime2.sdk.util.is_collection_type(type_):
            if k in signature.inputs:
                processed_inputs[k] = [sdk.Artifact.load(x) for x in v]
            elif v == []:
                if signature.parameters[k].has_default():
                    processed_inputs[k] = signature.parameters[k].default
                else:
                    raise NotImplementedError("Empty list given, but no"
                                              " default can be used")
            else:
                processed_inputs[k] = v

            if type_.name == 'Set' and processed_inputs[k] is not None:
                processed_inputs[k] = set(processed_inputs[k])

        elif qiime2.sdk.util.is_metadata_type(type_):
            if type_.name == 'MetadataColumn':
                value = (inputs[f'{k}_source_data'], inputs[k])
            else:
                value = inputs[k]

            processed_inputs[k] = _convert_metadata(type_, value)

        elif k in signature.inputs:
            processed_inputs[k] = sdk.Artifact.load(v)

        else:
            processed_inputs[k] = v

    return processed_inputs


@error_handler(header="This plugin encountered an error:\n")
def _execute_action(action, action_kwargs):
    for param, arg in action_kwargs.items():
        pretty_arg = repr(arg)
        if isinstance(arg, qiime2.sdk.Result):
            pretty_arg = str(arg.uuid)
        elif isinstance(arg, qiime2.Metadata):
            pretty_arg = "<Metadata>"
        elif isinstance(arg, list) or isinstance(arg, set):
            if len(arg) > 0 and isinstance(list(arg)[0], qiime2.sdk.Result):
                pretty_arg = ',\n'.join(str(a.uuid) for a in arg)
            else:
                pretty_arg = ', '.join(repr(a) for a in arg)
        line = f'｢{param}: {pretty_arg}｣'
        print(line, file=sys.stdout)
    print(" " * GALAXY_TRIMMED_STRING_LEN)  # see _error_handler for rational

    return action(**action_kwargs)


@error_handler(header="Unexpected error saving results in q2galaxy: ")
def _save_results(results):
    for name, result in zip(results._fields, results):
        location = result.save(name)
        print(f"Saved {result.type} to: {location}", file=sys.stdout)


def _convert_metadata(input_, value):
    if input_.name == 'MetadataColumn':
        value, column = value
        # Galaxy writes data_columns as lists in the JSON for reasons I assume.
        column, = column
        if column is None:
            return None
    fp = value

    if fp is None:
        return None

    try:
        artifact = qiime2.Artifact.load(fp)
    except Exception:
        try:
            metadata = qiime2.Metadata.load(fp)
        except Exception as e:
            raise ValueError("There was an issue with loading the file %s as "
                             "metadata:" % fp) from e
    else:
        try:
            metadata = artifact.view(qiime2.Metadata)
        except Exception as e:
            raise ValueError("There was an issue with viewing the artifact "
                             "%s as QIIME 2 Metadata:" % fp) from e

    if input_.name != 'MetadataColumn':
        return metadata
    else:
        try:
            # galaxy is 1-indexed and includes the ID column, so subtract 2
            column = list(metadata.columns.keys())[int(column) - 2]
            metadata_column = metadata.get_column(column)
        except Exception:
            raise ValueError("There was an issue with retrieving column %r "
                             "from the metadata." % column)

        if metadata_column not in input_:
            raise ValueError("Metadata column is of type %r, but expected %r."
                             % (metadata_column, input_.fields[0]))

        return metadata_column
