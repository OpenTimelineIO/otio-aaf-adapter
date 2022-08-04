OpenTimelineIO Advanced Authoring Format (AAF) Adapter
=====================================

[![Supported VFX Platform Versions](https://img.shields.io/badge/vfx%20platform-2018--2021-lightgrey.svg)](http://www.vfxplatform.com/)
![Supported Versions](https://img.shields.io/badge/python-2.7%2C%203.7%2C%203.8%2C%203.9%2C%203.10-blue)
[![Run tests](https://github.com/markreidvfx/otio-aaf-adapter/actions/workflows/ci.yaml/badge.svg)](https://github.com/OpenTimelineIO/otio-aaf-adapter/actions/workflows/ci.yaml)


Feature Matrix
--------------

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



Testing for Development
-----------------------

```bash
# In the root folder of the repo
pip install -e .

# Test adapter
otioconvert -i some_timeline.aaf -o some_timeline.ext
```
