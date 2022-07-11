
# AAF

```
OpenTimelineIO Advanced Authoring Format (AAF) Adapter

Depending on if/where PyAAF is installed, you may need to set this env var:
    OTIO_AAF_PYTHON_LIB - should point at the PyAAF module.
```

*source*: `otio_aaf_adapter/adapters/advanced_authoring_format.py`


*Supported Features (with arguments)*:

- read_from_file: 
```
Reads AAF content from `filepath` and outputs an OTIO
  timeline object.

  Args:
      filepath (str): AAF filepath
      simplify (bool, optional): simplify timeline structure by stripping empty
  items
      transcribe_log (bool, optional): log activity as items are getting
  transcribed
      attach_markers (bool, optional): attaches markers to their appropriate items
                                       like clip, gap. etc on the track
      bake_keyframed_properties (bool, optional): bakes animated property values
                                                  for each frame in a source clip
  Returns:
      otio.schema.Timeline
```
  - filepath
  - simplify
  - transcribe_log
  - attach_markers
  - bake_keyframed_properties
- write_to_file:
  - input_otio
  - filepath



