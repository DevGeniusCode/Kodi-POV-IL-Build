#!/usr/bin/env python3
"""Keep the baked ``userdata/favourites.xml`` in step with ``favourites_config.json``.

``plugin.program.orderfavourites-hebrew`` generates the favourites of a device from its JSON config
(``favourites_generator.generate_favourites_xml``). ``userdata/favourites.xml`` is only the fallback shipped in the
config pack: it is what a fresh device shows before the generator first runs, so it must be what the generator writes
for the default skin when no account service is connected (the generator fails closed outside Kodi: every tile with a
``condition`` is left out).

Checks (any failure exits 1):
  1. every key of every skin's ``order`` has a tile definition in ``tiles``;
  2. every ``inherit`` points to a skin that exists;
  3. the baked file lists the same tiles (name, icon, action) in the same order as the generator for the default skin.

``--write`` regenerates the baked file instead of comparing it (keeps the file's line endings).

Usage:  python .github/scripts/check_favourites.py [--write]
"""

from __future__ import annotations

import importlib.util
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
from kodi_addons import REPO_ROOT  # noqa: E402

GENERATOR = os.path.join(REPO_ROOT, "plugin.program.orderfavourites-hebrew", "favourites_generator.py")
BAKED = os.path.join(REPO_ROOT, "userdata", "favourites.xml")
DEFAULT_SKIN = "skin.fentastic"


def _load_generator():
    spec = importlib.util.spec_from_file_location("favourites_generator", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tiles(xml_text: str) -> list[tuple[str, str, str]]:
    root = ET.fromstring(xml_text)
    return [(f.get("name") or "", f.get("thumb") or "", (f.text or "").strip()) for f in root.findall("favourite")]


def _config_problems(gen, config) -> list[str]:
    problems = []
    tiles = config.get("tiles") or {}
    skins = config.get("skins") or {}
    for skin_id, entry in skins.items():
        parent = entry.get("inherit")
        if parent and parent not in skins:
            problems.append(f"skin '{skin_id}' inherits unknown skin '{parent}'")
        for key in gen._resolve_skin(config, skin_id).get("order") or []:
            if key not in tiles:
                problems.append(f"skin '{skin_id}': order key '{key}' has no tile definition")
    return problems


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    gen = _load_generator()
    config = gen._load_config()
    problems = _config_problems(gen, config)

    generated = gen.generate_favourites_xml(DEFAULT_SKIN, merge=False, write=False)

    if "--write" in sys.argv[1:]:
        if problems:
            print("\n".join(problems))
            return 1
        with open(BAKED, "rb") as fh:
            crlf = b"\r\n" in fh.read()
        text = generated.replace("\n", "\r\n") if crlf else generated
        with open(BAKED, "wb") as fh:
            fh.write(text.encode("utf-8"))
        print(f"wrote {BAKED} ({len(_tiles(generated))} tiles)")
        return 0

    with open(BAKED, "r", encoding="utf-8") as fh:
        baked = _tiles(fh.read())
    expected = _tiles(generated)
    if baked != expected:
        only_b = [t for t in baked if t not in expected]
        only_e = [t for t in expected if t not in baked]
        names_b = {t[0] for t in only_b}
        names_e = {t[0] for t in only_e}
        for name, _thumb, _action in only_e:
            if name in names_b:
                problems.append(f"favourites.xml tile '{name}' differs from the config (icon or action)")
            else:
                problems.append(f"favourites.xml lacks the tile '{name}'")
        for name, _thumb, _action in only_b:
            if name not in names_e:
                problems.append(f"favourites.xml has the tile '{name}', which the config does not produce")
        if not problems:
            problems.append("favourites.xml lists the tiles of the config in a different order")

    if problems:
        print("favourites.xml / favourites_config.json are out of step:")
        for p in problems:
            print("  - " + p)
        print("fix: python .github/scripts/check_favourites.py --write  (then bump config_version)")
        return 1
    print(f"favourites OK: {len(expected)} baked tiles match the config ({len(config.get('skins') or {})} skins checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
