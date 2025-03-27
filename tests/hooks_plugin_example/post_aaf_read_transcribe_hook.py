"""Example hook that runs post-transcription on a read operation.
This hook could be used to extract and transcode essence data from the AAF for
consumption outside of Avid MC.
"""
from otio_aaf_adapter.adapters.aaf_adapter.aaf_writer import AAFAdapterError


def hook_function(in_timeline, argument_map=None):
    if argument_map.get("test_post_hook_raise", False):
        raise AAFAdapterError()

    return in_timeline
