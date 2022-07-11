import argparse
import tempfile
import textwrap
import os

import opentimelineio as otio
import otio_aaf_adapter

PLUGIN_TEMPLATE = """
# {name}

```
{doc}
```

*source*: `{path}`

{other}

"""

ADAPTER_TEMPLATE = """
*Supported Features (with arguments)*:

{}

"""


def _format_plugin(plugin_map, extra_stuff, sanitized_paths):
    # XXX: always force unix path separator so that the output is consistent
    # between every platform.
    PATH_SEP = "/"

    path = plugin_map['path']

    # force using PATH_SEP in place of os.path.sep
    path = path.replace("\\", PATH_SEP)

    if sanitized_paths:
        path = PATH_SEP.join(path.split(PATH_SEP)[-3:])
    return PLUGIN_TEMPLATE.format(
        name=plugin_map['name'],
        doc=plugin_map['doc'],
        path=path,
        other=extra_stuff,
    )


def _format_doc(docstring, prefix):
    """Use textwrap to format a docstring for markdown."""

    initial_indent = prefix
    # subsequent_indent = " " * len(prefix)
    subsequent_indent = " " * 2

    block = docstring.split("\n")
    fmt_block = []
    for line in block:
        line = textwrap.fill(
            line,
            initial_indent=initial_indent,
            subsequent_indent=subsequent_indent,
            width=len(subsequent_indent) + 80,
        )
        initial_indent = subsequent_indent
        fmt_block.append(line)

    return "\n".join(fmt_block)


def _format_adapters(plugin_map):
    feature_lines = []

    for feature, feature_data in plugin_map['supported features'].items():
        doc = feature_data['doc']
        if doc:
            feature_lines.append(
                _format_doc(doc, "- {}: \n```\n".format(feature)) + "\n```"
            )
        else:
            feature_lines.append(
                "- {}:".format(feature)
            )

        for arg in feature_data["args"]:
            feature_lines.append("  - {}".format(arg))

    return ADAPTER_TEMPLATE.format("\n".join(feature_lines))


def _parsed_args():
    """ parse commandline arguments with argparse """

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-d",
        "--dryrun",
        action="store_true",
        default=False,
        help="Dryrun mode - print out instead of perform actions"
    )
    group.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Update the baseline with the current version"
    )

    return parser.parse_args()


def main():
    args = _parsed_args()

    manifest_path = os.path.abspath(os.path.join(otio_aaf_adapter.__file__,
                                                 '..', 'plugin_manifest.json'))
    manifest = otio.plugins.manifest_from_file(manifest_path)
    plugin_info_map = manifest.adapters[0].plugin_info_map()

    docs = _format_plugin(plugin_info_map,
                          _format_adapters(plugin_info_map), True)

    # print it out somewhere
    if args.dryrun:
        print(docs)
        return

    output = args.output
    if not output:
        output = tempfile.NamedTemporaryFile(
            'w',
            suffix="otio_serialized_schema.md",
            delete=False
        ).name

    with open(output, 'w') as fo:
        fo.write(docs)

    print("wrote documentation to {}.".format(output))


if __name__ == "__main__":
    main()
