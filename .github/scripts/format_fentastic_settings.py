#!/usr/bin/env python3
"""Format ``skin.fentastic``'s ``settings.xml`` into the canonical, categorized and sorted layout.

Kodi's skin-settings serializer drops XML comments and re-orders the file on every write, which makes diffs between two
exports unreadable. This tool puts the settings back under documented category blocks, sorted by ``id`` inside each
block, so the same configuration always produces the same bytes. It is a thin wrapper: the logic lives in
``script.fentastic.helper/resources/lib/modules/settings_sorter.py`` (also run inside Kodi by
``RunScript(script.fentastic.helper,mode=sort_settings)``), loaded here by path.

Every ``<setting>`` is copied as text; before anything is written the result is parsed back and compared setting by
setting (id, attributes, value) with the input, and formatted twice to prove the layout is stable. Line endings of the
file are kept (CRLF for a file without any). A duplicate id or a foreign element makes the tool stop without writing.

Usage:
  python .github/scripts/format_fentastic_settings.py [FILE ...]   # default: userdata/addon_data/skin.fentastic/settings.xml
  python .github/scripts/format_fentastic_settings.py --check       # exit 1 when a file is not already formatted
"""

from __future__ import annotations

import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from kodi_addons import REPO_ROOT  # noqa: E402

SORTER = os.path.join(REPO_ROOT, "script.fentastic.helper", "resources", "lib", "modules", "settings_sorter.py")
DEFAULT_TARGET = os.path.join(REPO_ROOT, "userdata", "addon_data", "skin.fentastic", "settings.xml")


def _load_sorter():
    spec = importlib.util.spec_from_file_location("fentastic_settings_sorter", SORTER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv):
    check = "--check" in argv
    paths = [a for a in argv if not a.startswith("--")] or [DEFAULT_TARGET]
    sorter = _load_sorter()
    failed = False
    for path in paths:
        try:
            changed, count = sorter.format_file(path, write=not check)
        except (OSError, sorter.SettingsFormatError) as exc:
            print("ERROR %s: %s" % (path, exc))
            failed = True
            continue
        if check and changed:
            print("NOT FORMATTED %s (%d settings); run: python .github/scripts/format_fentastic_settings.py" % (path, count))
            failed = True
        else:
            print("%s %s (%d settings)" % ("ok" if check or not changed else "formatted", path, count))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
