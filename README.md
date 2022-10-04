# OpenTimelineIO Advanced Authoring Format (AAF) Adapter

[![Supported VFX Platform Versions](https://img.shields.io/badge/vfx%20platform-2020--2023-lightgrey.svg)](http://www.vfxplatform.com/)
![Supported Versions](https://img.shields.io/badge/python-3.7%2C%203.8%2C%203.9%2C%203.10-blue)
[![Run tests](https://github.com/markreidvfx/otio-aaf-adapter/actions/workflows/ci.yaml/badge.svg)](https://github.com/OpenTimelineIO/otio-aaf-adapter/actions/workflows/ci.yaml)

## Overview

This project is a [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO) adapter for reading and writing Advanced Authoring Format (AAF) files.
This adapter was originally included with OpenTimelineIO as a contrib adapter. It is in the process of being separated into this project to improve maintainability and reduced the dependencies of both projects.

## Feature Matrix

| Feature                  | Read  | Write |
| -------                  | ----  | ----- |
| Single Track of Clips    |  ✔   |   ✔   |
| Multiple Video Tracks    |  ✔   |   ✔   |
| Audio Tracks & Clips     |  ✔   |   ✔   |
| Gap/Filler               |  ✔   |   ✔   |
| Markers                  |  ✔   |   ✔   |
| Nesting                  |  ✔   |   ✔   |
| Transitions              |  ✔   |   ✔   |
| Audio/Video Effects      |  ✖   |   ✖   |
| Linear Speed Effects     |  ✔   |   ✖   |
| Fancy Speed Effects      |  ✖   |   ✖   |
| Color Decision List      |  ✖   |   ✖   |
| Image Sequence Reference |  ✖   |   ✖   |

## Requirements

* [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO)
* [pyaaf2](https://github.com/markreidvfx/pyaaf2)


## Licensing

This repository is licensed under the [Apache License, Version 2.0](LICENSE.md).

## Testing for Development

```bash
# In the root folder of the repo
pip install -e .

# Test adapter
otioconvert -i some_timeline.aaf -o some_timeline.ext
```

If you are using a version of OpentimelineIO that still has the AAF contrib adapter you may need to add the path of [plugin_manifest.json](./src/otio_aaf_adapter/plugin_manifest.json) to your `OTIO_PLUGIN_MANIFEST_PATH` [environment variable.](https://opentimelineio.readthedocs.io/en/latest/tutorials/otio-env-variables.html) This should override the contrib version.

## Contributions

If you have any suggested changes to the otio-aaf-adapter,
please provide them via [pull request](../../pulls) or [create an issue](../../issues) as appropriate.

All contributions to this repository must align with the contribution
[guidelines](https://opentimelineio.readthedocs.io/en/latest/tutorials/contributing.html)
of the OpenTimelineIO project.
