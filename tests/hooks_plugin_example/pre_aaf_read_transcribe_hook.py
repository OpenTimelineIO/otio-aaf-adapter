"""Example hook that runs pre-transcription on a read operation.
This can be useful for just-in-time modification of the AAF structure prior to
transcription.
"""
from otio_aaf_adapter.adapters.aaf_adapter.aaf_writer import AAFAdapterError


def hook_function(in_timeline, argument_map=None):
    if argument_map.get("test_pre_hook_raise", False):
        raise AAFAdapterError()

    return in_timeline
