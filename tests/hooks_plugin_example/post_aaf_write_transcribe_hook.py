"""Example hook that runs post-transcription on a write operation.
This hook is useful to clean up temporary files or metadata post-write.
"""
from otio_aaf_adapter.adapters.aaf_adapter.aaf_writer import AAFAdapterError


def hook_function(in_timeline, argument_map=None):
    if argument_map.get("test_post_hook_raise", False):
        raise AAFAdapterError()

    if not argument_map.get("embed_essence", False):
        # no essence embedding requested, skip the hook
        return in_timeline

    for clip in in_timeline.find_clips():
        # reset target URL to pre-conversion media, remove metadata
        original_url = clip.media_reference.metadata.pop("original_target_url")
        if original_url:
            clip.media_reference.target_url = original_url

    return in_timeline
