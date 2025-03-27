"""Example hook that runs pre-transcription on a write operation.
This can be useful for just-in-time transcoding of media references to DNX data /
WAVE audio files.
"""
import os
from pathlib import Path
from otio_aaf_adapter.adapters.aaf_adapter.aaf_writer import AAFAdapterError


def hook_function(in_timeline, argument_map=None):
    if argument_map.get("test_pre_hook_raise", False):
        raise AAFAdapterError()

    if not argument_map.get("embed_essence", False):
        # no essence embedding requested, skip the hook
        return in_timeline

    for clip in in_timeline.find_clips():
        # mock convert video media references, this could be done with ffmpeg
        if Path(clip.media_reference.target_url).suffix == ".mov":
            converted_url = Path(clip.media_reference.target_url).with_suffix(".dnx")
            clip.media_reference.metadata[
                "original_target_url"
            ] = clip.media_reference.target_url
            clip.media_reference.target_url = os.fspath(converted_url)

    return in_timeline
