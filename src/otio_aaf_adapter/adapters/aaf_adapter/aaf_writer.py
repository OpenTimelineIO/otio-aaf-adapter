# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the OpenTimelineIO project

"""AAF Adapter Transcriber

Specifies how to transcribe an OpenTimelineIO file into an AAF file.
"""
from . import hooks

from pathlib import Path
from typing import Tuple
from typing import List
from numbers import Rational

import aaf2
import aaf2.mobs
import abc
import uuid
import opentimelineio as otio
import os
import copy
import re
import logging

from typing import Dict, Any


AAF_PARAMETERDEF_PAN = aaf2.auid.AUID("e4962322-2267-11d3-8a4c-0050040ef7d2")
AAF_OPERATIONDEF_MONOAUDIOPAN = aaf2.auid.AUID("9d2ea893-0968-11d3-8a38-0050040ef7d2")
AAF_PARAMETERDEF_AVIDPARAMETERBYTEORDER = uuid.UUID(
    "c0038672-a8cf-11d3-a05b-006094eb75cb")
AAF_PARAMETERDEF_AVIDEFFECTID = uuid.UUID(
    "93994bd6-a81d-11d3-a05b-006094eb75cb")
AAF_PARAMETERDEF_AFX_FG_KEY_OPACITY_U = uuid.UUID(
    "8d56813d-847e-11d5-935a-50f857c10000")
AAF_PARAMETERDEF_LEVEL = uuid.UUID("e4962320-2267-11d3-8a4c-0050040ef7d2")
AAF_VVAL_EXTRAPOLATION_ID = uuid.UUID("0e24dd54-66cd-4f1a-b0a0-670ac3a7a0b3")
AAF_OPERATIONDEF_SUBMASTER = uuid.UUID("f1db0f3d-8d64-11d3-80df-006008143e6f")

logger = logging.getLogger(__name__)


def _is_considered_gap(thing):
    """Returns whether or not thiing can be considered gap.

    TODO: turns generators w/ kind "Slug" inito gap.  Should probably generate
          opaque black instead.
    """
    if isinstance(thing, otio.schema.Gap):
        return True

    if isinstance(thing, otio.schema.Clip) and isinstance(
            thing.media_reference,
            otio.schema.GeneratorReference
    ):
        if thing.media_reference.generator_kind in ("Slug",):
            return True
        else:
            raise otio.exceptions.NotSupportedError(
                "AAF adapter does not support generator references of kind"
                " '{}'".format(thing.media_reference.generator_kind)
            )

    return False


def _nearest_timecode(rate):
    supported_rates = (24.0,
                       25.0,
                       30.0,
                       60.0)
    nearest_rate = 0.0
    min_diff = float("inf")
    for valid_rate in supported_rates:
        if valid_rate == rate:
            return rate

        diff = abs(rate - valid_rate)
        if diff >= min_diff:
            continue

        min_diff = diff
        nearest_rate = valid_rate

    return nearest_rate


class AAFAdapterError(otio.exceptions.OTIOError):
    pass


class AAFValidationError(AAFAdapterError):
    pass


class AAFFileTranscriber:
    """
    AAFFileTranscriber

    AAFFileTranscriber manages the file-level knowledge during a conversion from
    otio to aaf. This includes keeping track of unique tapemobs and mastermobs.
    """

    def __init__(self, input_otio, aaf_file, embed_essence, create_edgecode, **kwargs):
        """
        AAFFileTranscriber requires an input timeline and an output pyaaf2 file handle.

        Args:
            input_otio(otio.schema.Timeline): an input OpenTimelineIO timeline
            aaf_file(aaf2.file.AAFFile): a pyaaf2 file handle to an output file
            embed_essence(bool): if `True`, media references will be embedded into AAF
            create_edgecode(bool): if `True` each clip will get an EdgeCode slot
                assigned that defines the Avid Frame Count Start / End.
        """
        self.aaf_file = aaf_file
        self.embed_essence = embed_essence
        self.create_edgecode = create_edgecode
        self.compositionmob = self.aaf_file.create.CompositionMob()
        self.compositionmob.name = input_otio.name
        self.compositionmob.usage = "Usage_TopLevel"
        self.aaf_file.content.mobs.append(self.compositionmob)
        self._unique_mastermobs = {}
        self._unique_tapemobs = {}
        self._clip_mob_ids_map = _gather_clip_mob_ids(input_otio, **kwargs)

        # transcribe timeline comments onto composition mob
        self._transcribe_user_comments(input_otio, self.compositionmob)
        self._transcribe_mob_attributes(input_otio, self.compositionmob)

    def _unique_mastermob(self, otio_clip):
        """Get a unique mastermob, identified by clip metadata mob id."""
        mob_id = self._clip_mob_ids_map.get(otio_clip)
        mastermob = self._unique_mastermobs.get(mob_id)
        if not mastermob:
            mastermob = self.aaf_file.create.MasterMob()
            mastermob.name = otio_clip.name
            mastermob.mob_id = aaf2.mobid.MobID(mob_id)
            self.aaf_file.content.mobs.append(mastermob)
            self._unique_mastermobs[mob_id] = mastermob

            # transcribe clip comments / mob attributes onto master mob
            self._transcribe_user_comments(otio_clip, mastermob)
            self._transcribe_mob_attributes(otio_clip, mastermob)

            # transcribe media reference comments / mob attributes onto master mob.
            # this might overwrite clip comments / attributes.
            self._transcribe_user_comments(otio_clip.media_reference, mastermob)
            self._transcribe_mob_attributes(otio_clip.media_reference, mastermob)

        return mastermob

    def _unique_tapemob(self, otio_clip):
        """Get a unique tapemob, identified by clip metadata mob id."""
        mob_id = self._clip_mob_ids_map.get(otio_clip)
        tapemob = self._unique_tapemobs.get(mob_id)
        if not tapemob:
            tapemob = self.aaf_file.create.SourceMob()
            tapemob.name = otio_clip.name
            tapemob.descriptor = self.aaf_file.create.ImportDescriptor()
            # If the edit_rate is not an integer, we need
            # to use drop frame with a nominal integer fps.
            edit_rate = otio_clip.visible_range().duration.rate
            timecode_fps = round(edit_rate)
            tape_slot, tape_timecode_slot = tapemob.create_tape_slots(
                otio_clip.name,
                edit_rate=otio_clip.visible_range().duration.rate,
                timecode_fps=round(otio_clip.visible_range().duration.rate),
                drop_frame=(edit_rate != timecode_fps)
            )
            timecode_start = int(
                otio_clip.media_reference.available_range.start_time.value
            )
            timecode_length = int(
                otio_clip.media_reference.available_range.duration.value
            )

            tape_timecode_slot.segment.start = int(timecode_start)
            tape_timecode_slot.segment.length = int(timecode_length)
            self.aaf_file.content.mobs.append(tapemob)
            self._unique_tapemobs[mob_id] = tapemob

            media = otio_clip.media_reference
            if isinstance(media, otio.schema.ExternalReference) and media.target_url:
                locator = self.aaf_file.create.NetworkLocator()
                locator['URLString'].value = media.target_url
                tapemob.descriptor["Locator"].append(locator)

        return tapemob

    def track_transcriber(self, otio_track):
        """Return an appropriate _TrackTranscriber given an otio track."""
        if otio_track.kind == otio.schema.TrackKind.Video:
            transcriber = VideoTrackTranscriber(self, otio_track,
                                                embed_essence=self.embed_essence,
                                                create_edgecode=self.create_edgecode)
        elif otio_track.kind == otio.schema.TrackKind.Audio:
            transcriber = AudioTrackTranscriber(self, otio_track,
                                                embed_essence=self.embed_essence,
                                                create_edgecode=self.create_edgecode)
        else:
            raise otio.exceptions.NotSupportedError(
                f"Unsupported track kind: {otio_track.kind}")
        return transcriber

    def add_timecode(self, input_otio, default_edit_rate):
        """
        Add CompositionMob level timecode track base on global_start_time
        if available, otherwise start is set to 0.
        """
        if input_otio.global_start_time:
            edit_rate = input_otio.global_start_time.rate
            start = int(input_otio.global_start_time.value)
        else:
            edit_rate = default_edit_rate
            start = 0

        slot = self.compositionmob.create_timeline_slot(edit_rate)
        slot.name = "TC"

        # indicated that this is the primary timecode track
        slot['PhysicalTrackNumber'].value = 1

        # timecode.start is in edit_rate units NOT timecode fps
        # timecode.fps is only really a hint for a NLE displays on
        # how to display the start frame index to the user.
        # currently only selects basic non drop frame rates
        timecode = self.aaf_file.create.Timecode()
        timecode.fps = int(_nearest_timecode(edit_rate))
        timecode.drop = False
        timecode.start = start
        slot.segment = timecode

    def _transcribe_user_comments(self, otio_item, target_mob):
        """Transcribes user comments on `otio_item` onto `target_mob` in AAF."""

        user_comments = otio_item.metadata.get("AAF", {}).get("UserComments", {})
        for key, val in user_comments.items():
            if isinstance(val, (int, str)):
                target_mob.comments[key] = val
            elif isinstance(val, (float, Rational)):
                target_mob.comments[key] = aaf2.rational.AAFRational(val)
            else:
                logger.warning(
                    f"Skip transcribing unsupported comment value of type "
                    f"'{type(val)}' for key '{key}'."
                )

    def _transcribe_mob_attributes(self, otio_item, target_mob):
        """Transcribes mob attribute list onto the `target_mob`.
        This can be used to roundtrip specific mob config values, like audio channel
        settings.
        """
        mob_attr_map = otio_item.metadata.get("AAF", {}).get("MobAttributeList", {})
        mob_attr_list = aaf2.misc.TaggedValueHelper(target_mob['MobAttributeList'])
        for key, val in mob_attr_map.items():
            if isinstance(val, (int, str)):
                mob_attr_list[key] = val
            elif isinstance(val, (float, Rational)):
                mob_attr_list[key] = aaf2.rational.AAFRational(val)
            else:
                raise ValueError(f"Unsupported mob attribute type '{type(val)}' for "
                                 f"key '{key}'.")


def validate_metadata(timeline):
    """Print a check of necessary metadata requirements for an otio timeline."""

    all_checks = [__check(timeline, "duration().rate")]
    edit_rate = __check(timeline, "duration().rate").value

    for child in timeline.find_children():
        checks = []
        if _is_considered_gap(child):
            checks = [
                __check(child, "duration().rate").equals(edit_rate)
            ]
        if isinstance(child, otio.schema.Clip):
            checks = [
                __check(child, "duration().rate").equals(edit_rate),
                __check(child, "media_reference.available_range.duration.rate"
                        ).equals(edit_rate),
                __check(child, "media_reference.available_range.start_time.rate"
                        ).equals(edit_rate)
            ]
        if isinstance(child, otio.schema.Transition):
            checks = [
                __check(child, "duration().rate").equals(edit_rate),
                __check(child, "metadata['AAF']['PointList']"),
                __check(child, "metadata['AAF']['OperationGroup']['Operation']"
                               "['DataDefinition']['Name']"),
                __check(child, "metadata['AAF']['OperationGroup']['Operation']"
                               "['Description']"),
                __check(child, "metadata['AAF']['OperationGroup']['Operation']"
                               "['Name']"),
                __check(child, "metadata['AAF']['CutPoint']")
            ]
        all_checks.extend(checks)

    if any(check.errors for check in all_checks):
        raise AAFValidationError("\n" + "\n".join(
            sum([check.errors for check in all_checks], [])))


def _gather_clip_mob_ids(input_otio,
                         prefer_file_mob_id=False,
                         use_empty_mob_ids=False,
                         **kwargs):
    """
    Create dictionary of otio clips with their corresponding mob ids.
    """

    def _from_clip_metadata(clip):
        """Get the MobID from the clip.metadata."""
        return clip.metadata.get("AAF", {}).get("SourceID")

    def _from_media_reference_metadata(clip):
        """Get the MobID from the media_reference.metadata."""
        return (clip.media_reference.metadata.get("AAF", {}).get("MobID") or
                clip.media_reference.metadata.get("AAF", {}).get("SourceID"))

    def _from_aaf_file(clip):
        """ Get the MobID from the AAF file itself."""
        mob_id = None
        if isinstance(clip.media_reference, otio.schema.ExternalReference):
            target_url = clip.media_reference.target_url
            if os.path.isfile(target_url) and target_url.endswith("aaf"):
                with aaf2.open(clip.media_reference.target_url) as aaf_file:
                    mastermobs = list(aaf_file.content.mastermobs())
                    if len(mastermobs) == 1:
                        mob_id = mastermobs[0].mob_id
        return mob_id

    def _generate_empty_mobid(clip):
        """Generate a meaningless MobID."""
        return aaf2.mobid.MobID.new()

    strategies = [
        _from_clip_metadata,
        _from_media_reference_metadata,
        _from_aaf_file
    ]

    if prefer_file_mob_id:
        strategies.remove(_from_aaf_file)
        strategies.insert(0, _from_aaf_file)

    if use_empty_mob_ids:
        strategies.append(_generate_empty_mobid)

    clip_mob_ids = {}

    for otio_clip in input_otio.find_clips():
        if _is_considered_gap(otio_clip):
            continue
        for strategy in strategies:
            mob_id = strategy(otio_clip)
            if mob_id:
                clip_mob_ids[otio_clip] = mob_id
                break
        else:
            raise AAFAdapterError(f"Cannot find mob ID for clip {otio_clip}")

    return clip_mob_ids


def _stackify_nested_groups(timeline):
    """
    Ensure that all nesting in a given timeline is in a stack container.
    This conforms with how AAF thinks about nesting, there needs
    to be an outer container, even if it's just one object.
    """
    copied = copy.deepcopy(timeline)
    for track in copied.tracks:
        for i, child in enumerate(track.find_children()):
            is_nested = isinstance(child, otio.schema.Track)
            is_parent_in_stack = isinstance(child.parent(), otio.schema.Stack)
            if is_nested and not is_parent_in_stack:
                stack = otio.schema.Stack()
                track.remove(child)
                stack.append(child)
                track.insert(i, stack)
    return copied


class _TrackTranscriber:
    """
    _TrackTranscriber is the base class for the conversion of a given otio track.

    _TrackTranscriber is not meant to be used by itself. It provides the common
    functionality to inherit from. We need an abstract base class because Audio and
    Video are handled differently.
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, root_file_transcriber, otio_track,
                 embed_essence, create_edgecode):
        """
        _TrackTranscriber

        Args:
            root_file_transcriber(AAFFileTranscriber): the corresponding 'parent'
                AAFFileTranscriber object
            otio_track(otio.schema.Track): the given otio_track to convert
            embed_essence(bool): if `True`, referenced media files in clips will be
                embedded into the AAF file
            create_edgecode(bool): if `True` each clip will get an EdgeCode slot
                assigned that defines the Avid Frame Count Start / End.
        """
        self.root_file_transcriber = root_file_transcriber
        self.compositionmob = root_file_transcriber.compositionmob
        self.aaf_file = root_file_transcriber.aaf_file
        self.otio_track = otio_track
        self.edit_rate = self.otio_track.find_children()[0].duration().rate
        self.embed_essence = embed_essence
        self.create_edgecode = create_edgecode
        self.timeline_mobslot, self.sequence = self._create_timeline_mobslot()
        self.timeline_mobslot.name = self.otio_track.name

    def transcribe(self, otio_child):
        """Transcribe otio child to corresponding AAF object"""
        if _is_considered_gap(otio_child):
            filler = self.aaf_filler(otio_child)
            return filler
        elif isinstance(otio_child, otio.schema.Transition):
            transition = self.aaf_transition(otio_child)
            return transition
        elif isinstance(otio_child, otio.schema.Clip):
            source_clip = self.aaf_sourceclip(otio_child)
            return source_clip
        elif isinstance(otio_child, otio.schema.Track):
            sequence = self.aaf_sequence(otio_child)
            return sequence
        elif isinstance(otio_child, otio.schema.Stack):
            operation_group = self.aaf_operation_group(otio_child)
            return operation_group
        else:
            raise otio.exceptions.NotSupportedError(
                f"Unsupported otio child type: {type(otio_child)}")

    @property
    @abc.abstractmethod
    def media_kind(self) -> str:
        """Return the string for what kind of track this is."""
        pass

    @property
    @abc.abstractmethod
    def _master_mob_slot_id(self) -> int:
        """
        Return the MasterMob Slot ID for the corresponding track media kind
        """
        # MasterMob's and MasterMob slots have to be unique. We handle unique
        # MasterMob's with _unique_mastermob(). We also need to protect against
        # duplicate MasterMob slots. As of now, we mandate all picture clips to
        # be created in MasterMob slot 1 and all sound clips to be created in
        # MasterMob slot 2. While this is a little inadequate, it works for now
        pass

    @abc.abstractmethod
    def _create_timeline_mobslot(self) \
            -> Tuple[aaf2.mobslots.TimelineMobSlot, aaf2.components.Sequence]:
        """
        Return a timeline_mobslot and sequence for this track.

        In AAF, a TimelineMobSlot is a container for the Sequence. A Sequence is
        analogous to an otio track.

        Returns:
            Returns a tuple of (TimelineMobSlot, Sequence)
        """
        pass

    @abc.abstractmethod
    def default_descriptor(self, otio_clip) -> aaf2.essence.EssenceDescriptor:
        pass

    @abc.abstractmethod
    def _transition_parameters(self) -> \
            Tuple[List[aaf2.dictionary.ParameterDef], aaf2.misc.Parameter]:
        pass

    @abc.abstractmethod
    def _import_essence_for_clip(self,
                                 otio_clip: otio.schema.Clip,
                                 essence_path: Path) \
            -> Tuple[aaf2.mobs.MasterMob, aaf2.mobslots.TimelineMobSlot]:
        pass

    def aaf_network_locator(self, otio_external_ref):
        locator = self.aaf_file.create.NetworkLocator()
        locator['URLString'].value = otio_external_ref.target_url
        return locator

    def _copy_essence_for_clip(self,
                               otio_clip: otio.schema.Clip,
                               aaf_file_path: Path) \
            -> Tuple[aaf2.mobs.MasterMob, aaf2.mobslots.TimelineMobSlot]:
        # get Mob ID and make make sure it's a valid MobID object type
        mob_id = self.root_file_transcriber._clip_mob_ids_map.get(otio_clip)
        if isinstance(mob_id, str):
            urn_str = mob_id
            mob_id = aaf2.mobs.MobID()
            mob_id.urn = urn_str

        # open source AAF file and copy essence
        with aaf2.open(str(aaf_file_path), "r") as src_aaf:
            # copy over master mob and essence from source AAF to target AAF
            for src_master_mob in src_aaf.content.mastermobs():
                if src_master_mob.mob_id != mob_id:
                    continue

                # copy the essence data from file src_aaf to target aaf
                for i, slot in enumerate(src_master_mob.slots):
                    if isinstance(
                            slot, aaf2.mobslots.TimelineMobSlot
                    ):
                        # copy essence from file aaf_file_path to target aaf
                        src_source_mob = slot.segment.mob
                        essence_data_copy = src_source_mob.essence.copy(
                            root=self.aaf_file
                        )
                        self.aaf_file.content.essencedata.append(essence_data_copy)

                        # copy source mob from file aaf_file_path to target aaf
                        src_source_mob = slot.segment.mob
                        source_mob_copy = src_source_mob.copy(root=self.aaf_file)
                        self.aaf_file.content.mobs.append(source_mob_copy)
                        break
                else:
                    raise AAFAdapterError(
                        f"No essence data to copy for MasterMob with "
                        f"ID '{mob_id}' in media reference AAF file: {aaf_file_path}"
                    )

                # copy master mob from file aaf_file_path to target aaf
                master_mob_copy = src_master_mob.copy(root=self.aaf_file)
                self.aaf_file.content.mobs.append(master_mob_copy)

                # get timeline slot for master mob
                for slot in master_mob_copy.slots:
                    if isinstance(
                            slot, aaf2.mobslots.TimelineMobSlot
                    ):
                        master_mob_copy_tl_slot = slot
                        break
                else:
                    raise AAFAdapterError(f"No TimelineMobSlot for MasterMob "
                                          f"with ID '{mob_id}'.")

                break
            else:
                raise AAFAdapterError(f"No matching MasterMob with ID '{mob_id}' "
                                      f"in media reference AAF file: {aaf_file_path}")

            return master_mob_copy, master_mob_copy_tl_slot

    def aaf_filler(self, otio_gap):
        """Convert an otio Gap into an aaf Filler"""
        length = int(otio_gap.visible_range().duration.value)
        filler = self.aaf_file.create.Filler(self.media_kind, length)
        return filler

    def aaf_sourceclip(self, otio_clip):
        """Convert an OTIO Clip into a pyaaf SourceClip.
        If `self.embed_essence` is `True`, we attempt to import / embed
        the media reference target URL file into the new AAF as media essence.

        Args:
            otio_clip(otio.schema.Clip): input OTIO clip

        Returns:
            `aaf2.components.SourceClip`

        """
        if self.embed_essence and not otio_clip.media_reference.is_missing_reference:
            # embed essence for clip media
            target_path = Path(
                otio.url_utils.filepath_from_url(otio_clip.media_reference.target_url)
            )
            if not target_path.is_file():
                raise FileNotFoundError(f"Cannot find file to embed essence from: "
                                        f"'{target_path}'")

            if target_path.suffix == ".aaf":
                # copy over mobs and essence from existing AAF file
                mastermob, mastermob_slot = self._copy_essence_for_clip(
                    otio_clip, target_path
                )
            elif target_path.suffix in (".dnx", ".wav"):
                # import essence from clip media reference
                mastermob, mastermob_slot = self._import_essence_for_clip(
                    otio_clip=otio_clip, essence_path=target_path
                )
            else:
                raise AAFAdapterError(
                    f"Cannot embed media reference at: '{target_path}'."
                    f"Only .aaf / .dnx / .wav files are supported."
                    f"You can add logic to transcode your media for "
                    f"embedding by implementing a "
                    f"'{hooks.HOOK_PRE_WRITE_TRANSCRIBE}' hook.")
        else:
            tapemob, tapemob_slot = self._create_tapemob(otio_clip)
            filemob, filemob_slot = self._create_filemob(otio_clip, tapemob,
                                                         tapemob_slot)
            mastermob, mastermob_slot = self._create_mastermob(otio_clip,
                                                               filemob,
                                                               filemob_slot)

        # We need both `start_time` and `duration`
        # Here `start` is the offset between `first` and `in` values.

        offset = (otio_clip.visible_range().start_time -
                  otio_clip.available_range().start_time)
        start = int(offset.value)
        length = int(otio_clip.visible_range().duration.value)

        compmob_clip = self.compositionmob.create_source_clip(
            slot_id=self.timeline_mobslot.slot_id,
            # XXX: Python3 requires these to be passed as explicit ints
            start=int(start),
            length=int(length),
            media_kind=self.media_kind
        )
        compmob_clip.mob = mastermob
        compmob_clip.slot = mastermob_slot
        compmob_clip.slot_id = mastermob_slot.slot_id

        # create edgecode for Avid Frame Count properties
        if self.create_edgecode:
            ec_tl_slot = self._create_edgecode_timeline_slot(
                edit_rate=self.edit_rate,
                start=int(otio_clip.available_range().start_time.value),
                length=int(otio_clip.available_range().duration.value)
            )
            mastermob.slots.append(ec_tl_slot)

        # check if we need to set mark-in / mark-out
        if otio_clip.visible_range() != otio_clip.available_range():
            mastermob_slot["MarkIn"].value = int(
                otio_clip.visible_range().start_time.value
            )
            mastermob_slot["MarkOut"].value = int(
                otio_clip.visible_range().end_time_exclusive().value
            )

        return compmob_clip

    def aaf_transition(self, otio_transition):
        """Convert an otio Transition into an aaf Transition"""
        if (otio_transition.transition_type !=
                otio.schema.TransitionTypes.SMPTE_Dissolve):
            print(
                "Unsupported transition type: {}".format(
                    otio_transition.transition_type))
            return None

        transition_params, varying_value = self._transition_parameters()

        interpolation_def = self.aaf_file.create.InterpolationDef(
            aaf2.misc.LinearInterp, "LinearInterp", "Linear keyframe interpolation")
        self.aaf_file.dictionary.register_def(interpolation_def)
        varying_value["Interpolation"].value = (
            self.aaf_file.dictionary.lookup_interperlationdef("LinearInterp"))

        pointlist = otio_transition.metadata["AAF"]["PointList"]

        c1 = self.aaf_file.create.ControlPoint()
        c1["EditHint"].value = "Proportional"
        c1.value = pointlist[0]["Value"]
        c1.time = pointlist[0]["Time"]

        c2 = self.aaf_file.create.ControlPoint()
        c2["EditHint"].value = "Proportional"
        c2.value = pointlist[1]["Value"]
        c2.time = pointlist[1]["Time"]

        varying_value["PointList"].extend([c1, c2])

        op_group_metadata = otio_transition.metadata["AAF"]["OperationGroup"]
        effect_id = op_group_metadata["Operation"].get("Identification")
        is_time_warp = op_group_metadata["Operation"].get("IsTimeWarp")
        by_pass = op_group_metadata["Operation"].get("Bypass")
        number_inputs = op_group_metadata["Operation"].get("NumberInputs")
        operation_category = op_group_metadata["Operation"].get("OperationCategory")
        data_def = self.aaf_file.dictionary.lookup_datadef(str(self.media_kind))

        description = op_group_metadata["Operation"]["Description"]
        op_def_name = otio_transition.metadata["AAF"][
            "OperationGroup"
        ]["Operation"]["Name"]

        # Create OperationDefinition
        op_def = self.aaf_file.create.OperationDef(uuid.UUID(effect_id), op_def_name)
        self.aaf_file.dictionary.register_def(op_def)
        op_def.media_kind = self.media_kind
        datadef = self.aaf_file.dictionary.lookup_datadef(self.media_kind)
        op_def["IsTimeWarp"].value = is_time_warp
        op_def["Bypass"].value = by_pass
        op_def["NumberInputs"].value = number_inputs
        op_def["OperationCategory"].value = str(operation_category)
        op_def["ParametersDefined"].extend(transition_params)
        op_def["DataDefinition"].value = data_def
        op_def["Description"].value = str(description)

        # Create OperationGroup
        length = int(otio_transition.duration().value)
        operation_group = self.aaf_file.create.OperationGroup(op_def, length)
        operation_group["DataDefinition"].value = datadef
        operation_group["Parameters"].append(varying_value)

        # Create Transition
        transition = self.aaf_file.create.Transition(self.media_kind, length)
        transition["OperationGroup"].value = operation_group
        transition["CutPoint"].value = otio_transition.metadata["AAF"]["CutPoint"]
        transition["DataDefinition"].value = datadef
        return transition

    def aaf_sequence(self, otio_track):
        """Convert an otio Track into an aaf Sequence"""
        sequence = self.aaf_file.create.Sequence(media_kind=self.media_kind)
        sequence.components.value = []
        length = 0
        for nested_otio_child in otio_track:
            result = self.transcribe(nested_otio_child)
            length += result.length
            sequence.components.append(result)
        sequence.length = length
        return sequence

    def aaf_operation_group(self, otio_stack):
        """
        Create and return an OperationGroup which will contain other AAF objects
        to support OTIO nesting
        """
        # Create OperationDefinition
        op_def = self.aaf_file.create.OperationDef(AAF_OPERATIONDEF_SUBMASTER,
                                                   "Submaster")
        self.aaf_file.dictionary.register_def(op_def)
        op_def.media_kind = self.media_kind
        datadef = self.aaf_file.dictionary.lookup_datadef(self.media_kind)

        # These values are necessary for pyaaf2 OperationDefinitions
        op_def["IsTimeWarp"].value = False
        op_def["Bypass"].value = 0
        op_def["NumberInputs"].value = -1
        op_def["OperationCategory"].value = "OperationCategory_Effect"
        op_def["DataDefinition"].value = datadef

        # Create OperationGroup
        operation_group = self.aaf_file.create.OperationGroup(op_def)
        operation_group.media_kind = self.media_kind
        operation_group["DataDefinition"].value = datadef

        length = 0
        for nested_otio_child in otio_stack:
            result = self.transcribe(nested_otio_child)
            length += result.length
            operation_group.segments.append(result)
        operation_group.length = length
        return operation_group

    def _create_tapemob(self, otio_clip):
        """
        Return a physical sourcemob for an otio Clip based on the MobID.

        Returns:
            Returns a tuple of (TapeMob, TapeMobSlot)
        """
        tapemob = self.root_file_transcriber._unique_tapemob(otio_clip)
        tapemob_slot = tapemob.create_empty_slot(self.edit_rate, self.media_kind)
        tapemob_slot.segment.length = int(
            otio_clip.media_reference.available_range.duration.value)
        return tapemob, tapemob_slot

    def transcribe_otio_aaf_descriptor(
        self,
        descriptor: aaf2.essence.FileDescriptor,
        otio_aaf_descriptor: Dict[str, Any],
    ) -> aaf2.essence.FileDescriptor:
        """
        Transcribe the properties of AAF descriptor if the are not yet mapped.

        Args:
            descriptor (_type_): AAF descriptor to be transcribed.
            otio_aaf_descriptor (_type_): OTIO representation of the descriptor.

        Returns:
            descriptor: The default-transcribed AAF descriptor extended with
            the properties from otio_aaf_descriptor.
        """
        for key, value in otio_aaf_descriptor.items():
            # ClassName is not an AAF property
            if key == "ClassName":
                continue

            # Don't overwrite already set properties
            if key in descriptor:
                continue

            try:
                key_typedef = descriptor[key].typedef
                key_native_type = self.aaf_file.metadict.lookup_class(
                    key_typedef.class_id
                )

                if (
                    key_native_type is aaf2.types.TypeDefRecord
                    and key_typedef.type_name == "AUID"
                ):
                    key_native_value = aaf2.types.AUID(value)
                    descriptor[key].value = key_native_value
                else:
                    descriptor[key].value = value
            except KeyError as e:
                logger.warning(f'Translation of "{key}" is impossible: {e}')

        return descriptor

    def _create_filemob(self, otio_clip, tapemob, tapemob_slot):
        """
        Return a file sourcemob for an otio Clip. Needs a tapemob and tapemob slot.

        Returns:
            Returns a tuple of (FileMob, FileMobSlot)
        """
        filemob = self.aaf_file.create.SourceMob()
        self.aaf_file.content.mobs.append(filemob)

        filemob.descriptor = self.default_descriptor(otio_clip)
        filemob_slot = filemob.create_timeline_slot(self.edit_rate)
        filemob_clip = filemob.create_source_clip(
            slot_id=filemob_slot.slot_id,
            length=tapemob_slot.segment.length,
            media_kind=tapemob_slot.segment.media_kind)
        filemob_clip.mob = tapemob
        filemob_clip.slot = tapemob_slot
        filemob_clip.slot_id = tapemob_slot.slot_id
        filemob_slot.segment = filemob_clip
        return filemob, filemob_slot

    def _create_mastermob(self, otio_clip, filemob, filemob_slot):
        """
        Return a mastermob for an otio Clip. Needs a filemob and filemob slot.

        Returns:
            Returns a tuple of (MasterMob, MasterMobSlot)
        """
        mastermob = self.root_file_transcriber._unique_mastermob(otio_clip)
        timecode_length = int(otio_clip.media_reference.available_range.duration.value)

        try:
            mastermob_slot = mastermob.slot_at(self._master_mob_slot_id)
        except IndexError:
            mastermob_slot = (
                mastermob.create_timeline_slot(edit_rate=self.edit_rate,
                                               slot_id=self._master_mob_slot_id))
        mastermob_clip = mastermob.create_source_clip(
            slot_id=mastermob_slot.slot_id,
            length=timecode_length,
            media_kind=self.media_kind)
        mastermob_clip.mob = filemob
        mastermob_clip.slot = filemob_slot
        mastermob_clip.slot_id = filemob_slot.slot_id
        mastermob_slot.segment = mastermob_clip
        return mastermob, mastermob_slot

    def _create_edgecode_timeline_slot(self, edit_rate, start, length):
        """Creates and edgecode timeline mob slot, which is needed
        to set Frame Count Start and Frame Count End values in Avid.

        Args:
            aaf_file(aaf2.AAFFile): AAF file handle
            edit_rate(Fraction): fractional edit rate
            start(int): Frame Count Start frame number
            length(int): clip length

        Returns:
            aaf2.TimelineMobSlot: edgecode TL mob slot

        """
        edgecode = self.aaf_file.create.EdgeCode()
        edgecode.media_kind = "Edgecode"
        edgecode["Start"].value = start
        edgecode["Length"].value = length
        edgecode["AvEdgeType"].value = 3
        edgecode["AvFilmType"].value = 0
        edgecode["FilmKind"].value = "Ft35MM"
        edgecode["CodeFormat"].value = "EtNull"

        ec_tl_slot = self.aaf_file.create.TimelineMobSlot(slot_id=20,
                                                          edit_rate=edit_rate)
        ec_tl_slot.name = "EC1"
        ec_tl_slot.segment = edgecode

        # Important magic number from Avid,
        # track number has to be 6 otherwise MC will ignore it
        ec_tl_slot["PhysicalTrackNumber"].value = 6

        return ec_tl_slot


class VideoTrackTranscriber(_TrackTranscriber):
    """Video track kind specialization of TrackTranscriber."""

    @property
    def media_kind(self):
        return "picture"

    @property
    def _master_mob_slot_id(self):
        return 1

    def _create_timeline_mobslot(self):
        """
        Create a Sequence container (TimelineMobSlot) and Sequence.

        TimelineMobSlot --> Sequence
        """
        timeline_mobslot = self.compositionmob.create_timeline_slot(
            edit_rate=self.edit_rate)
        sequence = self.aaf_file.create.Sequence(media_kind=self.media_kind)
        sequence.components.value = []
        timeline_mobslot.segment = sequence
        return timeline_mobslot, sequence

    def default_descriptor(self, otio_clip):
        descriptor_dict = otio_clip.media_reference.metadata.get("AAF", {}).get(
            "EssenceDescription", {})

        descriptor_class = descriptor_dict.get("ClassName")
        if descriptor_class:
            descriptor = getattr(self.aaf_file.create, descriptor_class)()
        else:
            descriptor = self.aaf_file.create.CDCIDescriptor()
            descriptor_class = "CDCIDescriptor"

        video_linemap = descriptor_dict.get("VideoLineMap", [42, 0])
        video_linemap = [int(x) for x in video_linemap]

        if isinstance(descriptor, aaf2.essence.CDCIDescriptor):
            descriptor["ComponentWidth"].value = int(
                descriptor_dict.get("ComponentWidth", 8)
            )
            descriptor["HorizontalSubsampling"].value = int(
                descriptor_dict.get("HorizontalSubsampling", 2)
            )
        elif isinstance(descriptor, aaf2.essence.RGBADescriptor):
            # This is a hack for aaf2's inability of dealing with
            # empty pixel layout list that OTIO has
            default_pixel_layout = [
                {'Code': 'CompRed', 'Size': 8},
                {'Code': 'CompGreen', 'Size': 8},
                {'Code': 'CompBlue', 'Size': 8}
            ]
            pixel_layout = descriptor_dict.get("PixelLayout", default_pixel_layout)
            if (len(pixel_layout) == 0):
                pixel_layout = default_pixel_layout

            descriptor["PixelLayout"].value = pixel_layout

        descriptor["ImageAspectRatio"].value = descriptor_dict.get(
            "ImageAspectRatio", "16/9"
        )
        descriptor["StoredWidth"].value = int(
            descriptor_dict.get("StoredWidth", 1920)
        )
        descriptor["StoredHeight"].value = int(
            descriptor_dict.get("StoredHeight", 1080)
        )
        descriptor["FrameLayout"].value = descriptor_dict.get(
            "FrameLayout", "FullFrame"
        )
        descriptor["VideoLineMap"].value = video_linemap

        # aaf2 Rational follows python's fractions logic,
        # thus able to construct from anything
        descriptor["SampleRate"].value = str(aaf2.rational.AAFRational(
            descriptor_dict.get("SampleRate", 24)))
        descriptor["Length"].value = int(descriptor_dict.get("Length", 1))

        media = otio_clip.media_reference
        if isinstance(media, otio.schema.ExternalReference):
            if media.target_url:
                locator = self.aaf_network_locator(media)
                descriptor["Locator"].append(locator)
            if media.available_range:
                descriptor['SampleRate'].value = media.available_range.duration.rate
                descriptor["Length"].value = int(media.available_range.duration.value)

        # Finalize the descriptor with the rest of the properties
        descriptor = self.transcribe_otio_aaf_descriptor(descriptor, descriptor_dict)

        return descriptor

    def _transition_parameters(self):
        """
        Return video transition parameters
        """
        # Create ParameterDef for AvidParameterByteOrder
        byteorder_typedef = self.aaf_file.dictionary.lookup_typedef("aafUInt16")
        param_byteorder = self.aaf_file.create.ParameterDef(
            AAF_PARAMETERDEF_AVIDPARAMETERBYTEORDER,
            "AvidParameterByteOrder",
            "",
            byteorder_typedef)
        self.aaf_file.dictionary.register_def(param_byteorder)

        # Create ParameterDef for AvidEffectID
        avid_effect_typdef = self.aaf_file.dictionary.lookup_typedef("AvidBagOfBits")
        param_effect_id = self.aaf_file.create.ParameterDef(
            AAF_PARAMETERDEF_AVIDEFFECTID,
            "AvidEffectID",
            "",
            avid_effect_typdef)
        self.aaf_file.dictionary.register_def(param_effect_id)

        # Create ParameterDef for AFX_FG_KEY_OPACITY_U
        opacity_param_def = self.aaf_file.dictionary.lookup_typedef("Rational")
        opacity_param = self.aaf_file.create.ParameterDef(
            AAF_PARAMETERDEF_AFX_FG_KEY_OPACITY_U,
            "AFX_FG_KEY_OPACITY_U",
            "",
            opacity_param_def)
        self.aaf_file.dictionary.register_def(opacity_param)

        # Create VaryingValue
        opacity_u = self.aaf_file.create.VaryingValue()
        opacity_u.parameterdef = self.aaf_file.dictionary.lookup_parameterdef(
            "AFX_FG_KEY_OPACITY_U")
        opacity_u["VVal_Extrapolation"].value = AAF_VVAL_EXTRAPOLATION_ID
        opacity_u["VVal_FieldCount"].value = 1

        return [param_byteorder, param_effect_id], opacity_u

    def _import_essence_for_clip(self, otio_clip, essence_path):
        """Implements DNX video essence import"""
        available_range = otio_clip.media_reference.available_range
        start = int(available_range.start_time.value)
        length = int(available_range.duration.value)
        edit_rate = round(available_range.duration.rate)

        # create master mobs
        mastermob = self.root_file_transcriber._unique_mastermob(otio_clip)
        tape_mob = self.root_file_transcriber._unique_tapemob(otio_clip)
        tape_clip = tape_mob.create_source_clip(self._master_mob_slot_id, start=start)

        # import video essence
        mastermob_slot = mastermob.import_dnxhd_essence(path=str(essence_path),
                                                        edit_rate=edit_rate,
                                                        tape=tape_clip,
                                                        length=length,
                                                        offline=False)
        return mastermob, mastermob_slot


class AudioTrackTranscriber(_TrackTranscriber):
    """Audio track kind specialization of TrackTranscriber."""

    @property
    def media_kind(self):
        return "sound"

    @property
    def _master_mob_slot_id(self):
        return 2

    def aaf_sourceclip(self, otio_clip):
        # Parameter Definition
        typedef = self.aaf_file.dictionary.lookup_typedef("Rational")
        param_def = self.aaf_file.create.ParameterDef(AAF_PARAMETERDEF_PAN,
                                                      "Pan",
                                                      "Pan",
                                                      typedef)
        self.aaf_file.dictionary.register_def(param_def)
        interp_def = self.aaf_file.create.InterpolationDef(aaf2.misc.LinearInterp,
                                                           "LinearInterp",
                                                           "LinearInterp")
        self.aaf_file.dictionary.register_def(interp_def)

        # generate PointList for pan
        varying_value = self.aaf_file.create.VaryingValue()
        varying_value.parameterdef = param_def
        varying_value["Interpolation"].value = interp_def

        length = int(otio_clip.duration().value)

        # default pan points are mid pan
        default_points = [
            {
                "ControlPointSource": 2,
                "Time": f"0/{length}",
                "Value": "1/2",
            },
            {
                "ControlPointSource": 2,
                "Time": f"{length - 1}/{length}",
                "Value": "1/2",
            }
        ]
        cp_dict_list = otio_clip.metadata.get("AAF", {}).get("Pan", {}).get(
            "ControlPoints", default_points)

        for cp_dict in cp_dict_list:
            point = self.aaf_file.create.ControlPoint()
            point["Time"].value = aaf2.rational.AAFRational(cp_dict["Time"])
            point["Value"].value = aaf2.rational.AAFRational(cp_dict["Value"])
            point["ControlPointSource"].value = cp_dict["ControlPointSource"]
            varying_value["PointList"].append(point)

        opgroup = self.timeline_mobslot.segment
        opgroup.parameters.append(varying_value)

        return super().aaf_sourceclip(otio_clip)

    def _create_timeline_mobslot(self):
        """
        Create a Sequence container (TimelineMobSlot) and Sequence.
        Sequence needs to be in an OperationGroup.

        TimelineMobSlot --> OperationGroup --> Sequence
        """
        # TimelineMobSlot
        timeline_mobslot = self.compositionmob.create_sound_slot(
            edit_rate=self.edit_rate)
        # OperationDefinition
        opdef = self.aaf_file.create.OperationDef(AAF_OPERATIONDEF_MONOAUDIOPAN,
                                                  "Audio Pan")
        opdef.media_kind = self.media_kind
        opdef["NumberInputs"].value = 1
        self.aaf_file.dictionary.register_def(opdef)
        # OperationGroup
        total_length = int(sum([t.duration().value for t in self.otio_track]))
        opgroup = self.aaf_file.create.OperationGroup(opdef)
        opgroup.media_kind = self.media_kind
        opgroup.length = total_length
        timeline_mobslot.segment = opgroup
        # Sequence
        sequence = self.aaf_file.create.Sequence(media_kind=self.media_kind)
        sequence.components.value = []
        sequence.length = total_length
        opgroup.segments.append(sequence)
        return timeline_mobslot, sequence

    def default_descriptor(self, otio_clip):
        descriptor = self.aaf_file.create.PCMDescriptor()
        descriptor_dict = otio_clip.media_reference.metadata.get("AAF", {}).get(
            "EssenceDescription", {})

        sample_rate = float(aaf2.rational.AAFRational(
            descriptor_dict.get("SampleRate", 48000)))
        descriptor_dict["AverageBPS"] = int(descriptor_dict.get("AverageBPS", 96000))
        descriptor_dict["BlockAlign"] = int(descriptor_dict.get("BlockAlign", 2))
        descriptor_dict["QuantizationBits"] = int(
            descriptor_dict.get("QuantizationBits", 16)
        )

        if isinstance(otio_clip.media_reference, otio.schema.ExternalReference):
            locator = self.aaf_network_locator(otio_clip.media_reference)
            descriptor["Locator"].append(locator)

        descriptor_dict["AudioSamplingRate"] = float(aaf2.rational.AAFRational(
            descriptor_dict.get("AudioSamplingRate", 48000))
        )
        descriptor_dict["Channels"] = int(descriptor_dict.get("Channels", 1))
        descriptor_dict["SampleRate"] = sample_rate
        descriptor_dict["Length"] = int(descriptor_dict.get("Length", int(
            otio_clip.media_reference.available_range.duration.rescaled_to(
                sample_rate).value
        )))

        # Finalize the descriptor with the rest of the properties
        descriptor = self.transcribe_otio_aaf_descriptor(descriptor, descriptor_dict)

        return descriptor

    def _transition_parameters(self):
        """
        Return audio transition parameters
        """
        # Create ParameterDef for ParameterDef_Level
        def_level_typedef = self.aaf_file.dictionary.lookup_typedef("Rational")
        param_def_level = self.aaf_file.create.ParameterDef(AAF_PARAMETERDEF_LEVEL,
                                                            "ParameterDef_Level",
                                                            "",
                                                            def_level_typedef)
        self.aaf_file.dictionary.register_def(param_def_level)

        # Create VaryingValue
        level = self.aaf_file.create.VaryingValue()
        level.parameterdef = (
            self.aaf_file.dictionary.lookup_parameterdef("ParameterDef_Level"))

        return [param_def_level], level


class __check:
    """
    __check is a private helper class that safely gets values given to check
    for existence and equality
    """

    def __init__(self, obj, tokenpath):
        self.orig = obj
        self.value = obj
        self.errors = []
        self.tokenpath = tokenpath
        try:
            for token in re.split(r"[\.\[]", tokenpath):
                if token.endswith("()"):
                    self.value = getattr(self.value, token.replace("()", ""))()
                elif "]" in token:
                    self.value = self.value[token.strip("[]'\"")]
                else:
                    self.value = getattr(self.value, token)
        except Exception as e:
            self.value = None
            self.errors.append("{}{} {}.{} does not exist, {}".format(
                self.orig.name if hasattr(self.orig, "name") else "",
                type(self.orig),
                type(self.orig).__name__,
                self.tokenpath, e))

    def equals(self, val):
        """Check if the retrieved value is equal to a given value."""
        if self.value is not None and self.value != val:
            self.errors.append(
                "{}{} {}.{} not equal to {} (expected) != {} (actual)".format(
                    self.orig.name if hasattr(self.orig, "name") else "",
                    type(self.orig),
                    type(self.orig).__name__, self.tokenpath, val, self.value))
        return self
