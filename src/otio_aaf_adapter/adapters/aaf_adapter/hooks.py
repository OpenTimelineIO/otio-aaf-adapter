# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the OpenTimelineIO project

import aaf2
import opentimelineio as otio


# Plugin custom hook names
HOOK_PRE_READ_TRANSCRIBE = "otio_aaf_pre_read_transcribe"
HOOK_POST_READ_TRANSCRIBE = "otio_aaf_post_read_transcribe"
HOOK_PRE_WRITE_TRANSCRIBE = "otio_aaf_pre_write_transcribe"
HOOK_POST_WRITE_TRANSCRIBE = "otio_aaf_post_write_transcribe"


def run_pre_write_transcribe_hook(
    timeline: otio.schema.Timeline,
    write_filepath: str,
    aaf_handle: aaf2.file.AAFFile,
    embed_essence: bool,
    extra_kwargs: dict
) -> otio.schema.Timeline:
    """This hook runs on write, just before the timeline got translated to pyaaf2
    data."""
    if HOOK_PRE_WRITE_TRANSCRIBE in otio.hooks.names():
        extra_kwargs.update({
            "write_filepath": write_filepath,
            "aaf_handle": aaf_handle,
            "embed_essence": embed_essence,
        })
        return otio.hooks.run(HOOK_PRE_WRITE_TRANSCRIBE, timeline, extra_kwargs)
    return timeline


def run_post_write_transcribe_hook(
    timeline: otio.schema.Timeline,
    write_filepath: str,
    aaf_handle: aaf2.file.AAFFile,
    embed_essence: bool,
    extra_kwargs: dict
) -> otio.schema.Timeline:
    """This hook runs on write, just after the timeline gets translated to pyaaf2 data.
    """
    if HOOK_POST_WRITE_TRANSCRIBE in otio.hooks.names():
        extra_kwargs.update({
            "write_filepath": write_filepath,
            "aaf_handle": aaf_handle,
            "embed_essence": embed_essence,
        })
        return otio.hooks.run(HOOK_POST_WRITE_TRANSCRIBE, timeline, extra_kwargs)
    return timeline


def run_pre_read_transcribe_hook(
    read_filepath: str,
    aaf_handle: aaf2.file.AAFFile,
    extra_kwargs: dict
) -> None:
    """This hook runs on read, just before the timeline gets translated from pyaaf2
    to OTIO data. It can be useful to manipulate the AAF data directly before the
    transcribing occurs. The hook doesn't return a timeline, since it runs before the
    Timeline object has been transcribed."""
    if HOOK_PRE_WRITE_TRANSCRIBE in otio.hooks.names():
        extra_kwargs.update({
            "read_filepath": read_filepath,
            "aaf_handle": aaf_handle,
        })
        otio.hooks.run(HOOK_PRE_READ_TRANSCRIBE, tl=None, extra_args=extra_kwargs)


def run_post_read_transcribe_hook(
    timeline: otio.schema.Timeline,
    read_filepath: str,
    aaf_handle: aaf2.file.AAFFile,
    extra_kwargs: dict
) -> otio.schema.Timeline:
    """This hook runs on read, just after the timeline got translated to OTIO data,
    but before it is simplified. Possible use cases could be logic to extract and
    transcode media from the AAF.
    """
    if HOOK_POST_WRITE_TRANSCRIBE in otio.hooks.names():
        extra_kwargs.update({
            "read_filepath": read_filepath,
            "aaf_handle": aaf_handle
        })
        return otio.hooks.run(HOOK_POST_WRITE_TRANSCRIBE,
                              tl=timeline,
                              extra_args=extra_kwargs)
    return timeline
