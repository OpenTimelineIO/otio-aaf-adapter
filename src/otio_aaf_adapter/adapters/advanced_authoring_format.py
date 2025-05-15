# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the OpenTimelineIO project

"""OpenTimelineIO Advanced Authoring Format (AAF) Adapter

Depending on if/where PyAAF is installed, you may need to set this env var:
    OTIO_AAF_PYTHON_LIB - should point at the PyAAF module.
"""
import colorsys
import copy
import numbers
import os
import sys

import collections
import fractions
from typing import List

import opentimelineio as otio

lib_path = os.environ.get("OTIO_AAF_PYTHON_LIB")
if lib_path and lib_path not in sys.path:
    sys.path.insert(0, lib_path)

import aaf2  # noqa: E402
import aaf2.content  # noqa: E402
import aaf2.mobs  # noqa: E402
import aaf2.components  # noqa: E402
import aaf2.core  # noqa: E402
import aaf2.misc  # noqa: E402
from otio_aaf_adapter.adapters.aaf_adapter import aaf_writer  # noqa: E402
from otio_aaf_adapter.adapters.aaf_adapter import hooks  # noqa: E402


debug = False

# If enabled, output recursive traversal info of _transcribe() method.
_TRANSCRIBE_DEBUG = False

# bake keyframed parameter
_BAKE_KEYFRAMED_PROPERTIES_VALUES = False

_PROPERTY_INTERPOLATION_MAP = {
    aaf2.misc.ConstantInterp: "Constant",
    aaf2.misc.LinearInterp: "Linear",
    aaf2.misc.BezierInterpolator: "Bezier",
    aaf2.misc.CubicInterpolator: "Cubic",
}

_MOB_TIMELINE_CACHE = {}


def _transcribe_log(s, indent=0, always_print=False):
    if always_print or _TRANSCRIBE_DEBUG:
        print("{}{}".format(" " * indent, s))


class AAFAdapterError(otio.exceptions.OTIOError):
    """ Raised for AAF adatper-specific errors. """


def _get_parameter(item, parameter_name):
    values = {value.name: value for value in item.parameters.value}
    return values.get(parameter_name)


def _encoded_name(item):

    name = _get_name(item)
    return name.encode("utf-8", "replace")


def _get_name(item):
    if isinstance(item, aaf2.components.SourceClip):
        try:
            return item.mob.name or "Untitled SourceClip"
        except AttributeError:
            # Some AAFs produce this error:
            # RuntimeError: failed with [-2146303738]: mob not found
            return "SourceClip Missing Mob"
    if hasattr(item, 'name'):
        name = item.name
        if name:
            return name
    return _get_class_name(item)


def _get_class_name(item):
    if hasattr(item, "class_name"):
        return item.class_name
    else:
        return item.__class__.__name__


def _transcribe_property(prop, owner=None):
    if isinstance(prop, (str, numbers.Integral, float)):
        return prop
    elif isinstance(prop, dict):
        result = {}
        for key, value in prop.items():
            result[key] = _transcribe_property(value)
        return result
    elif isinstance(prop, set):
        return list(prop)
    elif isinstance(prop, list):
        result = {}
        for child in prop:
            if hasattr(child, "name"):
                if not child.name:
                    continue
                if isinstance(child, aaf2.misc.VaryingValue):
                    # keyframed values
                    control_points = []
                    for control_point in child["PointList"]:
                        try:
                            # Some values cannot be transcribed yet
                            control_points.append(
                                [
                                    control_point.time,
                                    _transcribe_property(control_point.value),
                                ]
                            )
                        except TypeError:
                            _transcribe_log(
                                "Unable to transcribe value for property: "
                                "'{}' (Type: '{}', Parent: '{}')".format(
                                    child.name, type(child), prop
                                )
                            )

                    # bake keyframe values for owner time range
                    baked_values = None
                    if _BAKE_KEYFRAMED_PROPERTIES_VALUES:
                        if isinstance(owner, aaf2.components.Component):
                            baked_values = []
                            for t in range(0, owner.length):
                                baked_values.append([t, child.value_at(t)])
                        else:
                            _transcribe_log(
                                "Unable to bake values for property: "
                                "'{}'. Owner: {}, Control Points: {}".format(
                                    child.name, owner, control_points
                                )
                            )

                    value_dict = {
                        "_aaf_keyframed_property": True,
                        "keyframe_values": control_points,
                        "keyframe_interpolation": _PROPERTY_INTERPOLATION_MAP.get(
                            child.interpolationdef.auid, "Linear"
                        ),
                        "keyframe_baked_values": baked_values
                    }
                    result[child.name] = value_dict

                elif hasattr(child, "value"):
                    # static value
                    result[child.name] = _transcribe_property(child.value, owner=owner)
            else:
                # @TODO: There may be more properties that we might want also.
                # If you want to see what is being skipped, turn on debug.
                if debug:
                    debug_message = "Skipping unrecognized property: '{}', parent '{}'"
                    _transcribe_log(debug_message.format(child, prop))
        return result
    elif isinstance(prop, aaf2.core.AAFObject):
        result = {}
        result["ClassName"] = _get_class_name(prop)
        for child in prop.properties():
            if isinstance(child, aaf2.properties.WeakRefProperty):
                continue
            result[child.name] = _transcribe_property(child.value, owner=child)
        return result
    else:
        return str(prop)


def _transcribe_aaf_object_properties(item):
    metadata = {}
    metadata["ClassName"] = _get_class_name(item)
    for prop in item.properties():
        if hasattr(prop, 'name') and hasattr(prop, 'value'):
            key = str(prop.name)
            value = prop.value
            metadata[key] = _transcribe_property(value, owner=item)
    return metadata


def _otio_color_from_hue(hue):
    """Return an OTIO marker color, based on hue in range of [0.0, 1.0].

    Args:
        hue (float): marker color hue value

    Returns:
        otio.schema.MarkerColor: converted / estimated marker color

    """
    if hue <= 0.04 or hue > 0.93:
        return otio.schema.MarkerColor.RED
    if hue <= 0.13:
        return otio.schema.MarkerColor.ORANGE
    if hue <= 0.2:
        return otio.schema.MarkerColor.YELLOW
    if hue <= 0.43:
        return otio.schema.MarkerColor.GREEN
    if hue <= 0.52:
        return otio.schema.MarkerColor.CYAN
    if hue <= 0.74:
        return otio.schema.MarkerColor.BLUE
    if hue <= 0.82:
        return otio.schema.MarkerColor.PURPLE
    return otio.schema.MarkerColor.MAGENTA


def _marker_color_from_string(color):
    """Tries to derive a valid marker color from a string.

    Args:
        color (str): color name (e.g. "Yellow")

    Returns:
        otio.schema.MarkerColor: matching color or `None`
    """
    if not color:
        return

    return getattr(otio.schema.MarkerColor, color.upper(), None)


def _convert_rgb_to_marker_color(rgb_dict):
    """Returns a matching OTIO marker color for a given AAF color string.

    Adapted from `get_nearest_otio_color()` in the `xges.py` adapter.

    Args:
        rgb_dict (dict): marker color as dict,
                         e.g. `"{'red': 41471, 'green': 12134, 'blue': 6564}"`

    Returns:
        otio.schema.MarkerColor: converted / estimated marker color

    """

    float_colors = {
        (1.0, 0.0, 0.0): otio.schema.MarkerColor.RED,
        (0.0, 1.0, 0.0): otio.schema.MarkerColor.GREEN,
        (0.0, 0.0, 1.0): otio.schema.MarkerColor.BLUE,
        (0.0, 0.0, 0.0): otio.schema.MarkerColor.BLACK,
        (1.0, 1.0, 1.0): otio.schema.MarkerColor.WHITE,
    }
    if not rgb_dict:
        return otio.schema.MarkerColor.RED

    # convert from UInt to float
    red = float(rgb_dict["red"]) / 65535.0
    green = float(rgb_dict["green"]) / 65535.0
    blue = float(rgb_dict["blue"]) / 65535.0
    rgb_float = (red, green, blue)

    # check for exact match
    marker_color = float_colors.get(rgb_float)
    if marker_color:
        return marker_color

    # try to get an approxiate match based on hue
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    nearest = None
    if saturation < 0.2:
        if lightness > 0.65:
            nearest = otio.schema.MarkerColor.WHITE
        else:
            nearest = otio.schema.MarkerColor.BLACK
    if nearest is None:
        if lightness < 0.13:
            nearest = otio.schema.MarkerColor.BLACK
        if lightness > 0.9:
            nearest = otio.schema.MarkerColor.WHITE
    if nearest is None:
        nearest = _otio_color_from_hue(hue)
        if nearest == otio.schema.MarkerColor.RED and lightness > 0.53:
            nearest = otio.schema.MarkerColor.PINK
        if (
            nearest == otio.schema.MarkerColor.MAGENTA
            and hue < 0.89
            and lightness < 0.42
        ):
            # some darker magentas look more like purple
            nearest = otio.schema.MarkerColor.PURPLE

    # default to red color
    return nearest or otio.schema.MarkerColor.RED


def _add_child(parent, child, source):
    if child is None:
        if debug:
            print(f"Adding null child? {source}")
    elif isinstance(child, otio.schema.Marker):
        parent.markers.append(child)
    else:
        # if isinstance(parent, o)

        parent.append(child)


def _transcribe_media_kind(media_kind):
    if media_kind == "Picture":
        return otio.schema.TrackKind.Video
    elif media_kind in ("SoundMasterTrack", "Sound"):
        return otio.schema.TrackKind.Audio
    else:
        # Timecode, Edgecode, Data, ...
        return f"AAF_{media_kind}"


def _resolve_slot_components(slot):
    if not slot:
        return []

    if isinstance(slot.segment, aaf2.components.Sequence):
        components = list(slot.segment['Components'].value)
    else:
        components = [slot.segment]

    return components


def _resolve_single_slot_component(slot, component_type=aaf2.components.SourceClip):
    for component in _resolve_slot_components(slot):
        if isinstance(component, component_type):
            return component


def _iter_slot_essence_groups(slot):
    for component in _resolve_slot_components(slot):
        if isinstance(component, aaf2.components.EssenceGroup):
            yield list(component['Choices'].value)
        else:
            yield [component]


def _get_source_mob_reference_chain(mob, slot, source_clip, chain):
    chain.append([mob, slot, source_clip])
    mob = source_clip.mob
    try:
        slot = source_clip.slot
    except IndexError:
        slot = None

    if mob and slot:
        source_clip = _resolve_single_slot_component(slot)
        if source_clip:
            _get_source_mob_reference_chain(mob, slot, source_clip, chain)

    return chain


def _get_mob_start_tc(mob):
    tc_slot = None
    tc_primary = None

    for slot in mob.slots:
        timecode = _resolve_single_slot_component(slot, aaf2.components.Timecode)
        if not timecode:
            continue

        if slot['PhysicalTrackNumber'].value == 1:
            tc_slot = slot
            tc_primary = timecode
            break

    if tc_primary:
        edit_rate = float(tc_slot.edit_rate)
        start = otio.opentime.RationalTime(tc_primary.start, edit_rate)
        length = otio.opentime.RationalTime(tc_primary.length, edit_rate)
        return otio.opentime.TimeRange(start, length)

    return None


def _get_source_clip_ranges(slot, source_clip, in_range, start_tc):
    edit_rate = float(slot.edit_rate)

    start = otio.opentime.RationalTime(source_clip.start, edit_rate)
    duration = otio.opentime.RationalTime(source_clip.length, edit_rate)

    if start_tc:
        start = start + start_tc.start_time.rescaled_to(edit_rate)
        duration_tc = start_tc.duration.rescaled_to(edit_rate)
        duration = max(duration, duration_tc)

    if in_range:
        start += in_range.start_time.rescaled_to(edit_rate)
        available_range = otio.opentime.TimeRange(start, duration)
        available_range = available_range.clamped(in_range)
    else:
        available_range = otio.opentime.TimeRange(start, duration)

    return available_range


def _transcribe_url(target_path):
    if not target_path.startswith("file://"):
        target_path = "file://" + target_path
    return target_path.replace("\\", "/")


def _transcribe_source_mob(mob, available_range,
                           global_start_time, indent):
    assert isinstance(mob, aaf2.mobs.SourceMob)

    metadata = _transcribe_aaf_object_properties(mob)

    target_urls = []
    for locator in mob.descriptor["Locator"].value:
        url = locator['URLString'].value
        if url:
            target_urls.append(_transcribe_url(url))
    target_urls = target_urls or [None]

    if isinstance(mob.descriptor, aaf2.essence.MultipleDescriptor):
        # stream protocol here
        pass

    name = mob.name or str(mob.mob_id)

    if global_start_time:
        start = available_range.start_time + global_start_time.start_time
        duration = available_range.duration
        available_range = otio.opentime.TimeRange(start, duration)

    media_references = []
    for target_path in target_urls:
        if target_path:
            ref = otio.schema.ExternalReference(target_url=target_path,
                                                available_range=available_range)
        else:
            ref = otio.schema.MissingReference(name=name,
                                               available_range=available_range)

        msg = "Creating {} for SourceMob for {}".format(
            ref.__class__.__name__,
            _encoded_name(mob)
        )
        _transcribe_log(msg, indent)
        ref.name = name
        ref.metadata["AAF"] = metadata
        media_references.append([name, ref])

    return media_references


def _transcribe_master_mob_slot(mob, metadata, source_range, media_references,
                                global_start_time, indent):
    assert isinstance(mob, aaf2.mobs.MasterMob)

    if global_start_time:
        start = source_range.start_time + global_start_time.start_time
        duration = source_range.duration
        source_range = otio.opentime.TimeRange(start, duration)

    clip = otio.schema.Clip(name=mob.name,
                            source_range=source_range)

    # This is custom behavior we probably shouldn't be doing this
    # keeping it here to maintain some compatibly
    unc_path = metadata.get("UserComments", {}).get("UNC Path", None)
    if unc_path:
        unc_path = _transcribe_url(unc_path)
        ref = otio.schema.ExternalReference(target_url=unc_path)
        ref.name = "UNC Path"
        ref.available_range = source_range
        media_references.insert(0, [ref.name, ref])
        msg = "Creating ExternalReference from UserComments for UNC Path"
        _transcribe_log(msg, indent)

    if media_references:
        references = collections.OrderedDict()
        references[otio.schema.Clip.DEFAULT_MEDIA_KEY] = media_references[0][1]

        for key, ref in media_references[1:]:
            name = key
            i = 1
            while key in references:
                key = name + "_%02d" % i
                i += 1
            references[key] = ref

        clip.set_media_references(references, otio.schema.Clip.DEFAULT_MEDIA_KEY)

    return clip


def _transcribe_master_mob(mob, parents, metadata, indent):
    mob_timeline = otio.schema.Timeline()
    mob_timeline.name = mob.name

    # if the user manually sets the timecode the mastermob
    # will have an timecode track that acts as a global offset
    global_start_time = _get_mob_start_tc(mob)

    for slot in mob.slots:
        edit_rate = float(slot.edit_rate)
        if isinstance(slot, aaf2.mobslots.EventMobSlot):
            slot_track = _transcribe(slot, parents + [mob], edit_rate, indent + 2)
            mob_timeline.tracks.append(slot_track)
            continue

        if not isinstance(slot, aaf2.mobslots.TimelineMobSlot):
            continue

        msg = f"Creating Track for TimelineMobSlot for {_encoded_name(slot)}"
        _transcribe_log(msg, indent)

        slot_metadata = _transcribe_aaf_object_properties(slot)
        slot_track = otio.schema.Track()
        slot_track.metadata["AAF"] = slot_metadata

        slot_track.kind = _transcribe_media_kind(slot.segment.media_kind)
        slot_track.name = slot.name or ""

        for essence_group in _iter_slot_essence_groups(slot):
            essence_group_items = []
            for component in essence_group:
                if not component.length:
                    continue
                media_kind = component.media_kind

                if not isinstance(component, aaf2.components.SourceClip):
                    gap = otio.schema.Gap()
                    gap.source_range = otio.opentime.TimeRange(
                        otio.opentime.RationalTime(0, edit_rate),
                        otio.opentime.RationalTime(component.length, edit_rate)
                    )
                    essence_group_items.append(gap)
                else:
                    chain = _get_source_mob_reference_chain(mob, slot, component, [])
                    in_range = None
                    media_references = []
                    for chain_mob, chain_slot, chain_source_clip in reversed(chain):
                        # don't use start_tc on mastermob
                        start_tc = _get_mob_start_tc(chain_mob) if isinstance(
                            chain_mob, aaf2.mobs.SourceMob) else None
                        available_range = _get_source_clip_ranges(
                            chain_slot, chain_source_clip, in_range, start_tc)
                        if isinstance(chain_mob, aaf2.mobs.SourceMob):
                            references = _transcribe_source_mob(
                                chain_mob, available_range,
                                global_start_time, indent + 2)
                            media_references.extend(references)
                        elif isinstance(chain_mob, aaf2.mobs.MasterMob):
                            # create clip
                            media_references.reverse()
                            clip = _transcribe_master_mob_slot(
                                chain_mob, metadata,
                                available_range, media_references,
                                global_start_time, indent + 2)
                            clip.metadata["AAF"] = metadata
                            clip.metadata["AAF"]["MediaKind"] = media_kind
                            essence_group_items.append(clip)

                        in_range = available_range

            if essence_group_items:
                # essence_group_stack = otio.schema.Stack()
                # for i, item in enumerate(essence_group_items):
                #     t = otio.schema.Track()
                #     t.kind = slot_track.kind
                #     t.append(item)
                #     t.enabled = i == 0
                #     essence_group_stack.append(t)
                # slot_track.append(essence_group_stack)

                # only and the first essence_group item
                msg = f"Creating {type(essence_group_items[0]).__name__} " \
                    + f"for {_encoded_name(component)}"
                _transcribe_log(msg, indent + 2)
                slot_track.append(essence_group_items[0])

        mob_timeline.tracks.append(slot_track)

    # master mobs don't have transitions so its safe to do this
    _attach_markers(mob_timeline, indent)
    return mob_timeline


def _transcribe(item, parents, edit_rate, indent=0):
    global _MOB_TIMELINE_CACHE
    result = None
    metadata = {}

    # First lets grab some standard properties that are present on
    # many types of AAF objects...
    metadata["Name"] = _get_name(item)
    if isinstance(item, aaf2.core.AAFObject):
        metadata.update(_transcribe_aaf_object_properties(item))

    if hasattr(item, 'media_kind'):
        metadata["MediaKind"] = str(item.media_kind)

    # Some AAF objects (like TimelineMobSlot) have an edit rate
    # which should be used for all of the object's children.
    # We will pass it on to any recursive calls to _transcribe()
    if hasattr(item, "edit_rate"):
        edit_rate = float(item.edit_rate)

    if isinstance(item, aaf2.components.Component):
        metadata["Length"] = item.length

    # Now we will use the item's class to determine which OTIO type
    # to transcribe into. Note that the order of this if/elif/... chain
    # is important, because the class hierarchy of AAF objects is more
    # complex than OTIO.

    if isinstance(item, aaf2.content.ContentStorage):
        msg = f"Creating SerializableCollection for {_encoded_name(item)}"
        _transcribe_log(msg, indent)
        result = otio.schema.SerializableCollection()

        for mob in item.compositionmobs():
            _transcribe_log("compositionmob traversal", indent)
            child = _transcribe(mob, parents + [item], edit_rate, indent + 2)
            _add_child(result, child, mob)

    elif isinstance(item, aaf2.mobs.SourceMob):
        err = "AAF parsing error: unexpected SourceMob"
        raise AAFAdapterError(err)

    elif isinstance(item, aaf2.mobs.MasterMob):
        result = _MOB_TIMELINE_CACHE.get(item.mob_id, None)
        if result:
            _transcribe_log(
                f"Reusing Timeline for MasterMob for {_encoded_name(item)}", indent)
        else:
            _transcribe_log(
                f"Creating Timeline for MasterMob for {_encoded_name(item)}", indent)
            result = _transcribe_master_mob(item, parents, metadata, indent + 2)
            _MOB_TIMELINE_CACHE[item.mob_id] = result

    elif isinstance(item, aaf2.mobs.CompositionMob):
        result = _MOB_TIMELINE_CACHE.get(item.mob_id, None)
        if result:
            _transcribe_log(
                f"Reusing Timeline for CompositionMob for {_encoded_name(item)}",
                indent)
        else:
            _transcribe_log(
                f"Creating Timeline for CompositionMob for {_encoded_name(item)}",
                indent)
            result = otio.schema.Timeline()

            for slot in item.slots:
                track = _transcribe(slot, parents + [item], edit_rate, indent + 2)
                _add_child(result.tracks, track, slot)

            # Use a heuristic to find the starting timecode from
            # this track and use it for the Timeline's global_start_time`
            timecode = _get_mob_start_tc(item)
            if timecode:
                result.global_start_time = timecode.start_time

            _MOB_TIMELINE_CACHE[item.mob_id] = result

    elif isinstance(item, aaf2.components.SourceClip):
        mob = item.mob
        slot_track = None
        mob_timeline = None

        duration = otio.opentime.RationalTime(item.length, edit_rate)
        source_range = otio.opentime.TimeRange(
            otio.opentime.RationalTime(item.start, edit_rate),
            duration
        )

        # find the source clip slot track
        if isinstance(mob, (aaf2.mobs.MasterMob, aaf2.mobs.CompositionMob)):
            mob_timeline = _transcribe(mob, list(), edit_rate, indent + 2)
            for track in mob_timeline.tracks:
                slot_id = track.metadata.get("AAF", {}).get("SlotID")
                if slot_id == item.slot_id:
                    slot_track = track.clone()
                    break

        # unable to resolve the mob or the slot
        if mob is None or slot_track is None:
            msg = f"Unable find slot_id: {item.slot_id} in mob {mob} creating Gap"
            _transcribe_log(msg, indent)
            print(msg)
            result = otio.schema.Gap()
            result.source_range = otio.opentime.TimeRange(
                otio.opentime.RationalTime(0, edit_rate),
                duration
            )

        elif isinstance(mob, aaf2.mobs.CompositionMob):
            assert isinstance(slot_track, otio.schema.Track)
            _transcribe_log(f"Creating Stack for {_encoded_name(item)}", indent)

            result = otio.schema.Stack()
            result.name = mob_timeline.name
            result.metadata["AAF"] = mob_timeline.metadata['AAF']
            result.metadata["AAF"]["MediaKind"] = str(item.media_kind)
            result.append(slot_track)
            result.source_range = source_range

            track_number = slot_track.metadata.get("AAF", {}).get("PhysicalTrackNumber")
            marker_track = None

            # also copy tracks tracks with markers
            for track in mob_timeline.tracks:
                slot_id = track.metadata.get("AAF", {}).get("SlotID")

                if slot_id == item.slot_id:
                    continue

                sub_tracks = [track]
                sub_tracks.extend(track.find_children(
                    descended_from_type=otio.schema.Track))

                for current_track in sub_tracks:
                    for marker in list(current_track.markers):
                        metadata = marker.metadata.get("AAF", {})
                        attached_slot_id = metadata.get("AttachedSlotID")
                        attached_track_number = metadata.get(
                            "AttachedPhysicalTrackNumber")
                        if (track_number == attached_track_number and
                                item.slot_id == attached_slot_id):
                            if not marker_track:
                                marker_track = otio.schema.Track()
                                marker_track.name = track.name
                                marker_track.kind = track.kind
                                gap_range = slot_track.available_range()
                                gap = otio.schema.Gap(source_range=gap_range)
                                marker_track.append(gap)
                            marker_track.markers.append(marker.clone())

            if marker_track:
                result.append(marker_track)

        else:
            _transcribe_log(f"Creating Track for {_encoded_name(item)}", indent)
            result = slot_track
            result.source_range = source_range

    elif isinstance(item, aaf2.components.Transition):
        _transcribe_log("Creating Transition for {}".format(
            _encoded_name(item)), indent)
        result = otio.schema.Transition()

        # Does AAF support anything else?
        result.transition_type = otio.schema.TransitionTypes.SMPTE_Dissolve

        op_group_item = item.getvalue('OperationGroup')
        if op_group_item:
            op_group = _transcribe(op_group_item, parents +
                                   [item], edit_rate, indent + 2)
            if op_group.effects:
                metadata["OperationGroup"] = op_group.effects[0].metadata.get("AAF", {})

        # Extract value and time attributes of both ControlPoints used for
        # creating AAF Transition objects
        varying_value = None
        for param in item.getvalue('OperationGroup').parameters:
            if isinstance(param, aaf2.misc.VaryingValue):
                varying_value = param
                break

        if varying_value is not None:
            for control_point in varying_value.getvalue('PointList'):
                value = control_point.value
                time = control_point.time
                metadata.setdefault('PointList', []).append({'Value': value,
                                                             'Time': time})

        in_offset = int(metadata.get("CutPoint", "0"))
        out_offset = item.length - in_offset
        result.in_offset = otio.opentime.RationalTime(in_offset, edit_rate)
        result.out_offset = otio.opentime.RationalTime(out_offset, edit_rate)

    elif isinstance(item, aaf2.components.Filler):
        _transcribe_log(f"Creating Gap for {_encoded_name(item)}", indent)
        result = otio.schema.Gap()

        length = item.length
        result.source_range = otio.opentime.TimeRange(
            otio.opentime.RationalTime(0, edit_rate),
            otio.opentime.RationalTime(length, edit_rate)
        )

    elif isinstance(item, aaf2.components.NestedScope):
        msg = f"Creating Stack for NestedScope for {_encoded_name(item)}"
        _transcribe_log(msg, indent)
        # TODO: Is this the right class?
        result = otio.schema.Stack()

        for slot in item.slots:
            child = _transcribe(slot, parents + [item], edit_rate, indent + 2)
            _add_child(result, child, slot)

    elif isinstance(item, aaf2.components.Sequence):
        msg = f"Creating Track for Sequence for {_encoded_name(item)}"
        _transcribe_log(msg, indent)
        result = otio.schema.Track()

        # if parent is a sequence add SlotID / PhysicalTrackNumber to attach markers
        parent = parents[-1]
        if isinstance(parent, (aaf2.components.Sequence, aaf2.components.NestedScope)):
            timeline_slots = [
                p for p in parents if isinstance(p, aaf2.mobslots.TimelineMobSlot)
            ]
            timeline_slot = timeline_slots[-1]
            if timeline_slot:
                if hasattr(parent, 'slots'):
                    slot_index = list(parent.slots).index(item) + 1
                    metadata["PhysicalTrackNumber"] = slot_index
                metadata["SlotID"] = int(timeline_slot["SlotID"].value)

        for component in item.components:
            child = _transcribe(component, parents + [item], edit_rate, indent + 2)
            _add_child(result, child, component)

    elif isinstance(item, aaf2.components.OperationGroup):
        msg = f"Creating operationGroup for {_encoded_name(item)}"
        _transcribe_log(msg, indent)
        result = _transcribe_operation_group(item, parents, metadata,
                                             edit_rate, indent + 2)

    elif isinstance(item, aaf2.mobslots.TimelineMobSlot):
        msg = f"Creating Track for TimelineMobSlot for {_encoded_name(item)}"
        _transcribe_log(msg, indent)
        result = otio.schema.Track()

        child = _transcribe(item.segment, parents + [item], edit_rate, indent + 2)

        _add_child(result, child, item.segment)

    elif isinstance(item, aaf2.mobslots.MobSlot):
        msg = f"Creating Track for MobSlot for {_encoded_name(item)}"
        _transcribe_log(msg, indent)
        result = otio.schema.Track()

        child = _transcribe(item.segment, parents + [item], edit_rate, indent + 2)
        _add_child(result, child, item.segment)

    elif isinstance(item, aaf2.components.Timecode):
        pass

    elif isinstance(item, aaf2.components.Pulldown):
        pass

    elif isinstance(item, aaf2.components.EdgeCode):
        pass

    elif isinstance(item, aaf2.components.ScopeReference):
        msg = f"Creating Gap for ScopedReference for {_encoded_name(item)}"
        _transcribe_log(msg, indent)
        # TODO: is this like FILLER?

        result = otio.schema.Gap()

        length = item.length
        result.source_range = otio.opentime.TimeRange(
            otio.opentime.RationalTime(0, edit_rate),
            otio.opentime.RationalTime(length, edit_rate)
        )

    elif isinstance(item, aaf2.components.DescriptiveMarker):
        event_mobs = [p for p in parents if isinstance(p, aaf2.mobslots.EventMobSlot)]
        if event_mobs:
            _transcribe_log(
                f"Create marker for '{_encoded_name(item)}'", indent
            )

            result = otio.schema.Marker()
            result.name = metadata["Comment"]

            event_mob = event_mobs[-1]

            metadata["AttachedSlotID"] = int(metadata["DescribedSlots"][0])
            metadata["AttachedPhysicalTrackNumber"] = int(
                event_mob["PhysicalTrackNumber"].value
            )

            # determine marker color
            color = _marker_color_from_string(
                metadata.get("CommentMarkerAttributeList", {}).get("_ATN_CRM_COLOR")
            )
            if color is None:
                color = _convert_rgb_to_marker_color(
                    metadata.get("CommentMarkerColor")
                )
            result.color = color

            position = metadata["Position"]

            # Length can be None, but the property will always exist
            # so get('Length', 1) wouldn't help.
            length = metadata["Length"]
            if length is None:
                length = 1

            result.marked_range = otio.opentime.TimeRange(
                start_time=otio.opentime.from_frames(position, edit_rate),
                duration=otio.opentime.from_frames(length, edit_rate),
            )
        else:
            _transcribe_log(
                "Cannot attach marker item '{}'. "
                "Missing event mob in hierarchy.".format(
                    _encoded_name(item)
                )
            )

    elif isinstance(item, aaf2.components.Selector):
        msg = f"Transcribe selector for  {_encoded_name(item)}"
        _transcribe_log(msg, indent)

        selected = item.getvalue('Selected')
        alternates = item.getvalue('Alternates', None)

        # First we check to see if the Selected component is either a Filler
        # or ScopeReference object, meaning we have to use the alternate instead
        if isinstance(selected, aaf2.components.Filler) or \
                isinstance(selected, aaf2.components.ScopeReference):

            # Safety check of the alternates list, then transcribe first object -
            # there should only ever be one alternate in this situation
            if alternates is None or len(alternates) != 1:
                err = "AAF Selector parsing error: object has unexpected number of " \
                      "alternates - {}".format(len(alternates))
                raise AAFAdapterError(err)
            result = _transcribe(alternates[0], parents + [item], edit_rate, indent + 2)

            # Filler/ScopeReference means the clip is muted/not enabled
            result.enabled = False

            # Muted tracks are handled in a slightly odd way so we need to do a
            # check here and pass the param back up to the track object
            # if isinstance(parents[-1], aaf2.mobslots.TimelineMobSlot):
            #     pass # TODO: Figure out mechanism for passing this up to parent

        else:

            # This is most likely a multi-cam clip
            result = _transcribe(selected, parents + [item], edit_rate, indent + 2)

            # Perform a check here to make sure no potential Gap objects
            # are slipping through the cracks
            if isinstance(result, otio.schema.Gap):
                err = f"AAF Selector parsing error: {type(item)}"
                raise AAFAdapterError(err)

            # A Selector can have a set of alternates to handle multiple options for an
            # editorial decision - we do a full parse on those obects too
            if alternates is not None:
                alternates = [
                    _transcribe(alt, parents + [item], edit_rate, indent + 2)
                    for alt in alternates
                ]

            metadata['alternates'] = alternates

    elif isinstance(item, collections.abc.Iterable):
        msg = "Creating SerializableCollection for Iterable for {}".format(
            _encoded_name(item))
        _transcribe_log(msg, indent)

        result = otio.schema.SerializableCollection()
        for child in item:
            result.append(_transcribe(child, parents + [item], edit_rate, indent + 2))
    else:
        # For everything else, we just ignore it.
        # To see what is being ignored, turn on the debug flag
        if debug:
            print(f"SKIPPING: {type(item)}: {item} -- {result}")

    # Did we get anything? If not, we're done
    if result is None:
        return None

    # Okay, now we've turned the AAF thing into an OTIO result
    # There's a bit more we can do before we're ready to return the result.

    # If we didn't get a name yet, use the one we have in metadata
    if not result.name and "Name" in metadata:
        result.name = metadata["Name"]

    # Attach the AAF metadata
    if not result.metadata:
        result.metadata.clear()

    if "AAF" not in result.metadata:
        result.metadata["AAF"] = metadata

    # Double check that we got the length we expected
    if isinstance(result, otio.core.Item):
        length = metadata.get("Length")
        if (
                length and
                result.source_range is not None and
                result.source_range.duration.value != length
        ):
            raise AAFAdapterError(
                "Wrong duration? {} should be {} {} {} in {}".format(
                    result.source_range.duration.value,
                    length,
                    item.length,
                    item,
                    result
                )
            )

    # Did we find a Track?
    if isinstance(result, otio.schema.Track):
        if hasattr(item, "media_kind"):
            result.kind = _transcribe_media_kind(item.media_kind)

    # Done!
    return result


def _transcribe_linear_timewarp(item, parameters):
    # this is a linear time warp
    effect = otio.schema.LinearTimeWarp()

    # we expect all effects passed in to have a SpeedRatio
    ratio = parameters.get("SpeedRatio")

    # There may also be keyframes on the speed graph or position graph.
    # Time warps with speed graph keyframes have these:
    #   ConstantValue:
    #     SpeedRatio
    #     AvidMotionPulldown
    #     AvidMotionInputFormat
    #     AvidMotionOutputFormat
    #   VaryingValue:
    #     PARAM_SPEED_MAP_U
    #     PARAM_SPEED_OFFSET_MAP_U
    # Time warps with position graph keyframes have these:
    #   ConstantValue:
    #     SpeedRatio
    #     AvidMotionPulldown
    #     AvidPhase
    #     AvidMotionInputFormat
    #     AvidMotionOutputFormat
    #   VaryingValue:
    #     PARAM_OFFSET_MAP_U
    # Do any time warps *not* have SpeedRatio?
    speed_offset_map = _get_parameter(item, 'PARAM_SPEED_OFFSET_MAP_U')
    # speed_map = _get_parameter(item, 'PARAM_SPEED_MAP_U')
    # offset_map = _get_parameter(item, 'PARAM_OFFSET_MAP_U')
    # TODO: We should also check the PARAM_OFFSET_MAP_U which has
    # an interpolation_def().name as well.

    # If we have just 2 control points, then
    # we can compute the time_scalar. Note that the SpeedRatio is
    # NOT correct in many AAFs - we aren't sure why, but luckily we
    # can compute the correct value this way.
    # Note: that this code ignores the interpolation type which is often
    # set to "Cubic" even when the curve is linear.
    # interpolation = speed_offset_map.interpolation.name if speed_offset_map else None
    points = speed_offset_map.get("PointList") if speed_offset_map else []
    if len(points) > 2:
        # This is something complicated... try the fancy version
        return _transcribe_fancy_timewarp(item, parameters)
    elif (
        len(points) == 2
    ):
        # With just two points, we can compute the slope
        effect.time_scalar = (
            float(points[1].value - points[0].value) /
            float(points[1].time - points[0].time)
        )
    else:
        # Fall back to the SpeedRatio if we didn't understand the points
        ratio = parameters.get("SpeedRatio")
        if ratio == str(item.length):
            # If the SpeedRatio == the length, this is a freeze frame
            effect.time_scalar = 0
        elif '/' in ratio:
            numerator, denominator = map(float, ratio.split('/'))
            # OTIO time_scalar is 1/x from AAF's SpeedRatio
            effect.time_scalar = denominator / numerator
        else:
            effect.time_scalar = 1.0 / float(ratio)

    # Is this is a freeze frame?
    if effect.time_scalar == 0:
        # Note: we might end up here if any of the code paths above
        # produced a 0 time_scalar.
        # Use the FreezeFrame class instead of LinearTimeWarp
        effect = otio.schema.FreezeFrame()

    return effect


def _transcribe_fancy_timewarp(item, parameters):

    # For now, this is an unsupported time effect...
    effect = otio.schema.TimeEffect()
    effect.effect_name = "Unknown Time Warp Effect"
    effect.name = item.get("Name", "")

    return effect

    # TODO: Here is some sample code that pulls out the full
    # details of a non-linear speed map.

    # speed_map = item.parameter['PARAM_SPEED_MAP_U']
    # offset_map = item.parameter['PARAM_SPEED_OFFSET_MAP_U']
    # Also? PARAM_OFFSET_MAP_U (without the word "SPEED" in it?)
    # print(speed_map['PointList'].value)
    # print(speed_map.count())
    # print(speed_map.interpolation_def().name)
    #
    # for p in speed_map.points():
    #     print("  ", float(p.time), float(p.value), p.edit_hint)
    #     for prop in p.point_properties():
    #         print("    ", prop.name, prop.value, float(prop.value))
    #
    # print(offset_map.interpolation_def().name)
    # for p in offset_map.points():
    #     edit_hint = p.edit_hint
    #     time = p.time
    #     value = p.value
    #
    #     pass
    #     # print "  ", float(p.time), float(p.value)
    #
    # for i in range(100):
    #     float(offset_map.value_at("%i/100" % i))
    #
    # # Test file PARAM_SPEED_MAP_U is AvidBezierInterpolator
    # # currently no implement for value_at
    # try:
    #     speed_map.value_at(.25)
    # except NotImplementedError:
    #     pass
    # else:
    #     raise


def _transcribe_operation_group(item, parents, metadata, edit_rate, indent):
    result = otio.schema.Stack()

    operation = metadata.get("Operation", {})
    parameters = metadata.get("Parameters", {})
    result.name = operation.get("Name")

    # Trust the length that is specified in the AAF
    length = metadata.get("Length")
    result.source_range = otio.opentime.TimeRange(
        otio.opentime.RationalTime(0, edit_rate),
        otio.opentime.RationalTime(length, edit_rate)
    )

    # Look for speed effects...
    effect = None
    if operation.get("IsTimeWarp"):
        if operation.get("Name") == "Motion Control":

            # if the effect has a SpeedRatio, we assume it's a linear time warp
            speed_ratio = _get_parameter(item, 'SpeedRatio')
            if speed_ratio is not None:
                effect = _transcribe_linear_timewarp(item, parameters)
            else:
                effect = _transcribe_fancy_timewarp(item, parameters)

        else:
            # Unsupported time effect
            effect = otio.schema.TimeEffect()
            effect.effect_name = ""
            effect.name = operation.get("Name")
    else:
        # Unsupported effect
        effect = otio.schema.Effect()
        effect.effect_name = ""
        effect.name = operation.get("Name")

    if effect is not None:
        result.effects.append(effect)

        effect.metadata.clear()
        effect.metadata.update({
            "AAF": {
                "Operation": operation,
                "Parameters": parameters
            }
        })

    for segment in item.getvalue("InputSegments", []):
        child = _transcribe(segment, parents + [item], edit_rate, indent)
        if child:
            _add_child(result, child, segment)

    return result


def _fix_transitions(thing):
    if isinstance(thing, otio.schema.Timeline):
        _fix_transitions(thing.tracks)
    elif (
        isinstance(thing, otio.core.Composition)
        or isinstance(thing, otio.schema.SerializableCollection)
    ):
        if isinstance(thing, otio.schema.Track):
            for c, child in enumerate(thing):

                # Don't touch the Transitions themselves,
                # only the Clips & Gaps next to them.
                if not isinstance(child, otio.core.Item):
                    continue

                # Was the item before us a Transition?
                if c > 0 and isinstance(
                    thing[c - 1],
                    otio.schema.Transition
                ):
                    pre_trans = thing[c - 1]

                    if child.source_range is None:
                        child.source_range = child.trimmed_range()
                    csr = child.source_range
                    child.source_range = otio.opentime.TimeRange(
                        start_time=csr.start_time + pre_trans.in_offset,
                        duration=csr.duration - pre_trans.in_offset
                    )

                # Is the item after us a Transition?
                if c < len(thing) - 1 and isinstance(
                    thing[c + 1],
                    otio.schema.Transition
                ):
                    post_trans = thing[c + 1]

                    if child.source_range is None:
                        child.source_range = child.trimmed_range()
                    csr = child.source_range
                    child.source_range = otio.opentime.TimeRange(
                        start_time=csr.start_time,
                        duration=csr.duration - post_trans.out_offset
                    )

        for child in thing:
            _fix_transitions(child)


def _find_child_at_time(target_track, start_time):
    """same as child_at_time but passes through transitions"""

    target_item = target_track.child_at_time(start_time)

    if isinstance(target_item, otio.schema.Transition):
        parent = target_item.parent()
        index = parent.index(target_item)
        before = parent[index - 1]

        start_local = target_track.transformed_time(
            start_time, parent)

        if before.range_in_parent().contains(start_local):
            target_item = before
        else:
            target_item = parent[index + 1]

        if isinstance(target_item, otio.core.Composition):
            start_local = parent.transformed_time(
                start_time, target_item)
            return _find_child_at_time(target_item, start_local)

    return target_item


def _attach_markers(collection, indent=0):
    """Search for markers on tracks and attach them to their corresponding item.

    Marked ranges will also be transformed into the new parent space.

    """
    # iterate all timeline objects
    if isinstance(collection, otio.schema.Timeline):
        timelines = [collection]
    else:
        timelines = collection.find_children(descended_from_type=otio.schema.Timeline)

    for timeline in timelines:
        tracks_map = {}

        # build track mapping
        for track in timeline.find_children(descended_from_type=otio.schema.Track):
            metadata = track.metadata.get("AAF", {})
            slot_id = metadata.get("SlotID")
            track_number = metadata.get("PhysicalTrackNumber")
            if slot_id is None or track_number is None:
                continue

            tracks_map[(int(slot_id), int(track_number))] = track

        # iterate all tracks for their markers and attach them to the matching item
        for current_track in timeline.find_children(
                descended_from_type=otio.schema.Track):
            for marker in list(current_track.markers):
                metadata = marker.metadata.get("AAF", {})
                slot_id = metadata.get("AttachedSlotID")
                track_number = metadata.get("AttachedPhysicalTrackNumber")
                target_track = tracks_map.get((slot_id, track_number))

                # remove marker from current parent track
                current_track.markers.remove(marker)

                # determine new item to attach the marker to
                if target_track is None:
                    # This can happen if you export from Avid with "Use Selected Tracks"
                    # where markers will not point at the correct PhysicalTrackNumber!
                    _transcribe_log(
                        f"Cannot find target track for marker: {marker}. "
                        "Adding to timeline."
                    )
                    # Lets add it directly to the timeline "stack" the same way
                    # OTIO files generated by DaVinci Resolve does.
                    target_item = timeline.tracks

                    # transform marked range into new item range
                    marked_start_local = current_track.transformed_time(
                        marker.marked_range.start_time, target_item
                    )

                    marker.marked_range = otio.opentime.TimeRange(
                        start_time=marked_start_local,
                        duration=marker.marked_range.duration,
                    )

                else:
                    try:
                        target_item = _find_child_at_time(
                            target_track, marker.marked_range.start_time)

                        if target_item is None or not hasattr(target_item, 'markers'):
                            # Item found cannot have markers, for example Transition.
                            # See also `marker-over-transition.aaf` in test data.
                            #
                            # Leave markers on the track for now.
                            _transcribe_log(
                                'Skip target_item `{}` cannot have markers'.format(
                                    target_item,
                                ),
                            )
                            target_item = target_track

                        # transform marked range into new item range
                        marked_start_local = current_track.transformed_time(
                            marker.marked_range.start_time, target_item
                        )

                        marker.marked_range = otio.opentime.TimeRange(
                            start_time=marked_start_local,
                            duration=marker.marked_range.duration
                        )

                    except otio.exceptions.CannotComputeAvailableRangeError as e:
                        # For audio media AAF file (marker-over-audio.aaf),
                        # this exception would be triggered in:
                        # `target_item = target_track.child_at_time()` with error
                        # message:
                        # "No available_range set on media reference on clip".
                        #
                        # Leave markers on the track for now.
                        _transcribe_log(
                            'Cannot compute availableRange from {} to {}: {}'.format(
                                marker,
                                target_track,
                                e,
                            ),
                        )
                        target_item = target_track

                # attach marker to target item
                target_item.markers.append(marker)

                _transcribe_log(
                    "{}Marker: '{}' (time: {}), attached to item: '{}'".format(
                        " " * indent,
                        marker.name,
                        marker.marked_range.start_time.value,
                        target_item.name,
                    )
                )

    return collection


def _ensure_stack_tracks(thing):
    if not isinstance(thing, otio.schema.Stack):
        return

    children_needing_tracks = []
    for child in thing:
        if isinstance(child, otio.schema.Track):
            continue
        children_needing_tracks.append(child)

    for child in children_needing_tracks:
        orig_index = thing.index(child)
        del thing[orig_index]
        new_track = otio.schema.Track()
        media_kind = child.metadata.get("AAF", {}).get("MediaKind", None)
        if media_kind:
            new_track.kind = _transcribe_media_kind(media_kind)
        new_track.name = child.name
        new_track.append(child)
        thing.insert(orig_index, new_track)


def _valuable_metadata(thing):
    metadata = thing.metadata.get("AAF", {})

    class_name = metadata.get("ClassName", None)
    if class_name in ('CompositionMob',) and metadata.get("UserComments", {}):
        return True


def _has_effects(thing):
    if isinstance(thing, otio.core.Item):
        if len(thing.effects) > 0:
            return True


def _has_time_effect(thing):
    for effect in thing.effects:
        is_time_warp = effect.metadata.get("AAF", {}).get(
            "Operation", {}).get("IsTimeWarp", False)
        if is_time_warp:
            return True


def _has_transitions(thing):
    if isinstance(thing, otio.schema.Track):
        thing = [thing]

    for child in thing:
        if child.find_children(descended_from_type=otio.schema.Transition,
                               shallow_search=True):
            return True
    return False


def _track_item_can_flatten(thing):
    if not isinstance(thing, otio.schema.Track):
        return False

    if _valuable_metadata(thing):
        return False

    if len(thing) == 1:
        return True
    elif _has_effects(thing):
        return False
    elif _has_transitions(thing):
        return False

    return True


def _stack_item_can_be_flatten(thing):

    if _has_effects(thing) or _valuable_metadata(thing):
        return False

    if isinstance(thing, otio.schema.Stack):
        # From:
        # [ ParentStack  [ ThingStack  [ TrackA ] ]
        #                          | - [ TrackB ] ]
        # To:
        # [ ParentStack [ TrackA ] ]
        #           | - [ TrackB ] ]
        return True

    elif isinstance(thing, otio.schema.Track):
        # From:
        # [ ParentStack [ ThingTrack [ StackA [ TrackA ] ] ] ]
        #                                 | - [ TrackB ] ] ] ]
        #
        # To:
        # [ ParentStack [ TrackA ] ]
        #           | - [ TrackB ] ]

        if len(thing) != 1:
            return False

        child_stack = thing[0]

        if not isinstance(child_stack, otio.schema.Stack):
            return False

        if _has_effects(child_stack) or _valuable_metadata(child_stack):
            return False

        return True

    return False


def _trim_markers(thing):
    remove_markers = []
    for marker in thing.markers:
        if not thing.source_range.contains(marker.marked_range.start_time):
            remove_markers.append(marker)

    for marker in remove_markers:
        thing.markers.remove(marker)

    return thing


def _fix_time_effect_duration(thing, source_range):
    if not source_range:
        return thing

    thing.source_range = otio.opentime.TimeRange(
        thing.source_range.start_time,
        source_range.duration
    )
    return thing


def _simplify(thing):
    # If the passed in is an empty dictionary or None, nothing to do.
    # Without this check it would still return thing, but this way we avoid
    # unnecessary if-chain compares.
    if not thing:
        return thing

    if isinstance(thing, otio.schema.SerializableCollection):
        if len(thing) == 1:
            return _simplify(thing[0])
        else:
            for c, child in enumerate(thing):
                thing[c] = _simplify(child)
            return thing

    elif isinstance(thing, otio.schema.Timeline):
        result = _simplify(thing.tracks)

        # Only replace the Timeline's stack if the simplified result
        # was also a Stack. Otherwise leave it (the contents will have
        # been simplified in place).
        if isinstance(result, otio.schema.Stack):
            thing.tracks = result

        return thing

    elif isinstance(thing, otio.core.Composition):
        # simplify our children
        for c, child in enumerate(thing):
            thing[c] = _simplify(child)

        if isinstance(thing, otio.schema.Track):

            c = len(thing) - 1
            while c >= 0:
                child = thing[c]

                if not _track_item_can_flatten(child):
                    c = c - 1
                    continue

                if child.source_range:
                    child = otio.algorithms.track_trimmed_to_range(
                        child, child.source_range)

                # Pull the child's children into the parent
                num = len(child)
                children_of_child = child[:]
                effects = child.effects[:]

                # clear out the ownership of 'child'
                del child[:]
                if num == 1:
                    first_item = children_of_child[0]
                    first_item.effects.extend(effects)

                    # if first item has time effect duration could shorter
                    # we need to maintain same duration as the child
                    if _has_time_effect(first_item):
                        first_item = _fix_time_effect_duration(
                            first_item, child.source_range)
                    children_of_child[0] = first_item

                thing[c:c + 1] = children_of_child

                # TODO: We may be discarding metadata, should we merge it?
                # TODO: Do we need to offset the markers in time?
                thing.markers.extend(child.markers)
                # Note: we don't merge effects, because we already made
                # sure the child had no effects in the if statement above.

                # Preserve the enabled/disabled state as we merge these two.
                thing.enabled = thing.enabled and child.enabled

                c = c + num - 1

        # remove empty children of Stacks
        elif isinstance(thing, otio.schema.Stack):
            for c in reversed(range(len(thing))):
                child = thing[c]
                if not _contains_something_valuable(child):
                    # TODO: We're discarding metadata... should we retain it?
                    del thing[c]

            # Look for Stacks within Stacks
            c = len(thing) - 1
            while c >= 0:
                child = thing[c]
                if _stack_item_can_be_flatten(child):
                    if isinstance(child, otio.schema.Track):
                        child = child[0]

                    # Pull the child's children into the parent
                    num = len(child)
                    children_of_child = child[:]
                    # clear out the ownership of 'child'
                    del child[:]
                    thing[c:c + 1] = children_of_child

                    # TODO: We may be discarding metadata, should we merge it?
                    # TODO: Do we need to offset the markers in time?
                    thing.markers.extend(child.markers)
                    # Note: we don't merge effects, because we already made
                    # sure the child had no effects in the if statement above.

                    # Preserve the enabled/disabled state as we merge these two.
                    thing.enabled = thing.enabled and child.enabled

                    c = c + num
                c = c - 1
            _ensure_stack_tracks(thing)

            # trim stack tracks if stack has a source_range set and
            # only if stack tracks don't contain transitions
            if thing.source_range and not _has_transitions(thing):
                for track_num, child_track in enumerate(thing):
                    thing[track_num] = otio.algorithms.track_trimmed_to_range(
                        child_track, thing.source_range)
                    for child in thing[track_num]:
                        _trim_markers(child)

                thing.source_range = otio.opentime.TimeRange(
                    duration=thing.source_range.duration)

        # skip redundant containers
        if _is_redundant_container(thing):
            # TODO: We may be discarding metadata here, should we merge it?
            result = thing[0].deepcopy()

            # As we are reducing the complexity of the object structure through
            # this process, we need to make sure that any/all enabled statuses
            # are being respected and applied in an appropriate way
            if not thing.enabled:
                result.enabled = False

            # TODO: Do we need to offset the markers in time?
            result.markers.extend(thing.markers)

            # TODO: The order of the effects is probably important...
            # should they be added to the end or the front?
            # Intuitively it seems like the child's effects should come before
            # the parent's effects. This will need to be solidified when we
            # add more effects support.
            result.effects.extend(thing.effects)
            # Keep the parent's length, if it has one
            if thing.source_range:
                # make sure it has a source_range first
                try:
                    if not result.source_range:
                        result.source_range = result.trimmed_range()
                    # modify the duration and combine start_times
                    result.source_range = otio.opentime.TimeRange(
                        result.source_range.start_time + thing.source_range.start_time,
                        thing.source_range.duration
                    )
                except otio.exceptions.CannotComputeAvailableRangeError:
                    result.source_range = copy.copy(thing.source_range)
            return result

    # if thing is the top level stack, all of its children must be in tracks
    if isinstance(thing, otio.schema.Stack) and thing.parent() is None:
        _ensure_stack_tracks(thing)

    return thing


def _is_redundant_container(thing):

    is_composition = isinstance(thing, otio.core.Composition)
    if not is_composition:
        return False

    has_one_child = len(thing) == 1
    if not has_one_child:
        return False

    if _valuable_metadata(thing):
        return False

    am_top_level_track = (
        type(thing) is otio.schema.Track
        and type(thing.parent()) is otio.schema.Stack
        and thing.parent().parent() is None
    )

    return (
        not am_top_level_track
        # am a top level track but my only child is a track
        or (
            type(thing) is otio.schema.Track
            and type(thing[0]) is otio.schema.Track
        )
    )


def _contains_something_valuable(thing):
    if isinstance(thing, otio.core.Item):
        if len(thing.effects) > 0 or len(thing.markers) > 0:
            return True

    if _valuable_metadata(thing):
        return True

    if isinstance(thing, otio.core.Composition):

        if len(thing) == 0:
            # NOT valuable because it is empty
            return False

        for child in thing:
            if _contains_something_valuable(child):
                # valuable because this child is valuable
                return True

        # none of the children were valuable, so thing is NOT valuable
        return False

    if isinstance(thing, otio.schema.Gap):
        # TODO: Are there other valuable things we should look for on a Gap?
        return False

    # anything else is presumed to be valuable
    return True


def _get_mobs_for_transcription(storage):
    """
    When we describe our AAF into OTIO space, we apply the following heuristic:

    1) First look for top level mobs and if found use that to transcribe.

    2) If we don't have top level mobs, look for composition mobs and use them to
    transcribe.

    3) Lastly if we don't have either, try to use master mobs to transcribe.

    If we don't find any Mobs, just tell the user and do transcrption on an empty
    list (to generate some 'empty-level' OTIO structure)

    This heuristic is based on 'real-world' examples. There may still be some
    corner cases / open questions (like could there be metadata on both
    a composition mob and master mob? And if so, who would 'win'?)

    In any way, this heuristic satisfies the current set of AAFs we are using
    in our test-environment.

    """

    top_level_mobs = list(storage.toplevel())

    if len(top_level_mobs) > 0:
        _transcribe_log("---\nTranscribing top level mobs\n---")
        return top_level_mobs

    composition_mobs = list(storage.compositionmobs())
    if len(composition_mobs) > 0:
        _transcribe_log("---\nTranscribing composition mobs\n---")
        return composition_mobs

    master_mobs = list(storage.mastermobs())
    if len(master_mobs) > 0:
        _transcribe_log("---\nTranscribing master mobs\n---")
        return master_mobs

    _transcribe_log("---\nNo mobs found to transcribe\n---")

    return []


def read_from_file(
    filepath: str,
    simplify: bool = True,
    transcribe_log: bool = False,
    attach_markers: bool = True,
    bake_keyframed_properties: bool = False,
    **kwargs
) -> otio.schema.Timeline:
    """Reads AAF content from `filepath` and outputs an OTIO timeline object.

    Args:
        filepath: AAF filepath
        simplify: simplify timeline structure by stripping empty items
        transcribe_log: log activity as items are getting transcribed
        attach_markers: attaches markers to their appropriate items
                                         like clip, gap. etc on the track
        bake_keyframed_properties: bakes animated property values
                                   for each frame in a source clip
    """
    # 'activate' transcribe logging if adapter argument is provided.
    # Note that a global 'switch' is used in order to avoid
    # passing another argument around in the _transcribe() method.
    #
    global _TRANSCRIBE_DEBUG, _BAKE_KEYFRAMED_PROPERTIES_VALUES, _MOB_TIMELINE_CACHE
    _TRANSCRIBE_DEBUG = transcribe_log
    _BAKE_KEYFRAMED_PROPERTIES_VALUES = bake_keyframed_properties
    _MOB_TIMELINE_CACHE = {}

    with aaf2.open(filepath) as aaf_file:
        # Note: We're skipping: aaf_file.header
        # Is there something valuable in there?

        # trigger adapter specific pre-transcribe read hook
        hooks.run_pre_read_transcribe_hook(
            read_filepath=filepath,
            aaf_handle=aaf_file,
            extra_kwargs=kwargs.get(
                "hook_function_argument_map", {}
            )
        )

        storage = aaf_file.content
        mobs_to_transcribe = _get_mobs_for_transcription(storage)

        timeline = _transcribe(mobs_to_transcribe, parents=list(), edit_rate=None)

        # trigger adapter specific post-transcribe read hook
        hooks.run_post_read_transcribe_hook(
            timeline=timeline,
            read_filepath=filepath,
            aaf_handle=aaf_file,
            extra_kwargs=kwargs.get(
                "hook_function_argument_map", {}
            )
        )

    # OTIO represents transitions a bit different than AAF, so
    # we need to iterate over them and modify the items on either side.
    # Note this needs to be done before attaching markers, marker
    # positions are not stored with transition length offsets
    _fix_transitions(timeline)

    # Attach marker to the appropriate clip, gap etc.
    if attach_markers:
        timeline = _attach_markers(timeline)

    # AAF is typically more deeply nested than OTIO.
    # Let's try to simplify the structure by collapsing or removing
    # unnecessary stuff.
    if simplify:
        timeline = _simplify(timeline)

    # Reset transcribe_log debugging
    _TRANSCRIBE_DEBUG = False
    _MOB_TIMELINE_CACHE = {}
    return timeline


def write_to_file(
    input_otio,
    filepath,
    prefer_file_mob_id=False,
    use_empty_mob_ids=False,
    embed_essence=False,
    create_edgecode=False,
    **kwargs
):
    """Serialize `input_otio` to an AAF file at `filepath`.

    Args:
        input_otio(otio.schema.Timeline): input timeline to serialize
        filepath(str): output filepath for .aaf file
        prefer_file_mob_id(Optional[bool]): Attempt to extract
            the Mob ID from referenced files first.
        use_empty_mob_ids(Optional[bool]): Do not extract Mob IDs from metadata
        embed_essence(Optional[bool]): if `True`, media essence will be included in AAF
        create_edgecode(Optional[bool]): if `True` each clip will get an EdgeCode slot
                assigned that defines the Avid Frame Count Start / End.
        **kwargs: extra adapter arguments
    """
    with aaf2.open(filepath, "w") as f:
        # trigger adapter specific pre-transcribe write hook
        hook_tl = hooks.run_pre_write_transcribe_hook(
            timeline=input_otio,
            write_filepath=filepath,
            aaf_handle=f,
            embed_essence=embed_essence,
            extra_kwargs=kwargs.get(
                "hook_function_argument_map", {}
            )
        )

        timeline = aaf_writer._stackify_nested_groups(hook_tl)

        aaf_writer.validate_metadata(timeline)

        otio2aaf = aaf_writer.AAFFileTranscriber(
            input_otio=timeline,
            aaf_file=f,
            prefer_file_mob_id=prefer_file_mob_id,
            use_empty_mob_ids=use_empty_mob_ids,
            embed_essence=embed_essence,
            create_edgecode=create_edgecode,
            **kwargs
        )

        if not isinstance(timeline, otio.schema.Timeline):
            raise otio.exceptions.NotSupportedError(
                "Currently only supporting top level Timeline")

        default_edit_rate = None
        for otio_track in timeline.tracks:
            # Ensure track must have clip to get the edit_rate
            if len(otio_track) == 0:
                continue

            transcriber = otio2aaf.track_transcriber(otio_track)
            if not default_edit_rate:
                default_edit_rate = transcriber.edit_rate

            for otio_child in otio_track:
                result = transcriber.transcribe(otio_child)
                if result:
                    transcriber.sequence.components.append(result)

        # Always add a timecode track to the main composition mob.
        # This is required for compatibility with DaVinci Resolve.
        if default_edit_rate or input_otio.global_start_time:
            otio2aaf.add_timecode(input_otio, default_edit_rate)

        # trigger adapter specific post-transcribe write hook
        hooks.run_post_write_transcribe_hook(
            timeline=timeline,
            write_filepath=filepath,
            aaf_handle=f,
            embed_essence=embed_essence,
            extra_kwargs=kwargs.get(
                "hook_function_argument_map", {}
            )
        )


def adapter_hook_names() -> List[str]:
    """Returns names of custom hooks implemented by this adapter."""
    return [
        hooks.HOOK_POST_READ_TRANSCRIBE,
        hooks.HOOK_POST_WRITE_TRANSCRIBE,
        hooks.HOOK_PRE_READ_TRANSCRIBE,
        hooks.HOOK_PRE_WRITE_TRANSCRIBE
    ]
