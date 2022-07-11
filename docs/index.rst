Welcome to otio-aaf-adapter's documentation!
==================================================

Reads and writes AAF compositions

- includes clip, gaps, transitions but not markers or effects
- This adapter is still in progress, see the ongoing work here: `AAF Project <https://github.com/AcademySoftwareFoundation/OpenTimelineIO/projects/1>`_
- `Spec <https://static.amwa.tv/ms-01-aaf-object-spec.pdf>`_
- `Protocol <https://static.amwa.tv/as-01-aaf-edit-protocol-spec.pdf>`_

- Depends on the `PyAAF2 <https://github.com/markreidvfx/pyaaf2>`_ module, so either:

    - ``pip install pyaaf2``
    - ...or set ``${OTIO_AAF_PYTHON_LIB}`` to point the location of the PyAAF2 module


Plugin Reference
----------------

.. toctree::
   :maxdepth: 1

   api/otio-plugins

Links
-----

- `OpenTimelineIO Home Page <http://opentimeline.io/>`_
- `OpenTimelineIO Discussion Group <https://lists.aswf.io/g/otio-discussion>`_


Indices and tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
