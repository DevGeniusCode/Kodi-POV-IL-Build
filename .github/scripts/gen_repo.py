#!/usr/bin/env python3
"""Generate the complete Kodi repository site (what GitHub Pages serves).

Usage::

    python .github/scripts/gen_repo.py --out site --base-url https://<owner>.github.io/<repo>

Output layout (``--out``)::

    index.html                          landing page + links Kodi's "install from zip" browser can follow
    .nojekyll
    addons.xml                          every add-on's addon.xml, wrapped in <addons>
    addons.xml.md5                      hex md5 of addons.xml (the repository <checksum>)
    repository.povil-latest.zip         stable-name copies for install instructions
    plugin.program.kodipovilwizard-latest.zip
    zips/<id>/<id>-<version>.zip        the <datadir>: Kodi downloads exactly this path
    zips/<id>/icon.*, fanart.*, ...     artwork declared in <assets> (Kodi shows it in the repo browser)
    zips/<id>/changelog-<version>.txt
    config/config-<cfgver>.zip          the userdata/ pack, applied by the Wizard (NOT an add-on)
    config/config.json                  {config_version, file, url, size, sha256} -- the Wizard's only state input

Why every add-on is rebuilt on every run: a Pages artifact REPLACES the whole site on each deploy, so there is no
"previous release" to carry zips over from. The zips are deterministic (sorted entries, fixed timestamps), so an
unchanged add-on yields the same bytes and the same Kodi-visible version each time.

The script is strict: it exits non-zero (and the deploy never happens) on a malformed addon.xml, a folder whose name
is not its id, a duplicate id, or a final site that fails ``verify()``.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import sys
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
import build_addons  # noqa: E402
import build_config  # noqa: E402
from kodi_addons import Addon, discover_addons  # noqa: E402

# Third-party repository add-ons that still live in this repo as folders. They are NOT published: the aggregating
# repository.povil (Phase 2) replaces them. Delete this set together with the folders (Phase 3).
THIRD_PARTY_REPOS = frozenset({
    "repository.709",
    "repository.Fishenzon",
    "repository.cocoscrapers",
    "repository.jurialmunkey",
    "repository.kodifitzwell",
    "repository.otaku",
    "repository.umbrella",
})

# Copied to <site>/<id>-latest.zip so install instructions can use a name that never changes.
STABLE_ALIASES = ("repository.povil", "plugin.program.kodipovilwizard")

# Files Kodi looks for next to the zips when <assets> does not name them.
DEFAULT_ASSETS = ("icon.png", "fanart.jpg")

VIRTUAL_PREFIXES = ("xbmc.", "kodi.")
BIG_FILE_WARN = 90 * 1024 * 1024
SITE_WARN = 800 * 1024 * 1024            # GitHub Pages hard limit is 1 GB

_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>\s*")
_LEAD_COMMENT_RE = re.compile(r"^\s*<!--.*?-->\s*", re.S)
_NUMERIC_VERSION_RE = re.compile(r"^\d+(\.\d+)*$")


class RepoError(Exception):
    """A condition that must stop the build."""


# --------------------------------------------------------------------------- helpers
def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def addon_xml_fragment(addon: Addon) -> str:
    """The addon.xml of ``addon`` as it must appear inside addons.xml.

    Textual, not re-serialized: the XML declaration and any comment ahead of the root are dropped, everything else
    is byte-for-byte the author's text (newlines normalized to LF). The result is parsed again and must still be an
    <addon> with the same id / version, so a broken file can never reach the index.
    """
    path = os.path.join(addon.path, "addon.xml")
    with open(path, "rb") as fh:
        text = fh.read().decode("utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _DECL_RE.sub("", text, count=1)
    while True:
        stripped = _LEAD_COMMENT_RE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    text = text.strip()
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise RepoError(f"{addon.id}: addon.xml is not valid XML after normalizing: {exc}")
    if root.tag != "addon" or root.get("id") != addon.id or root.get("version") != addon.version:
        raise RepoError(f"{addon.id}: normalized addon.xml root does not match id/version")
    return text


def asset_paths(addon: Addon) -> list[str]:
    """Relative artwork paths declared in <assets> (plus Kodi's defaults), safe to copy."""
    root = ET.parse(os.path.join(addon.path, "addon.xml")).getroot()
    found: set[str] = set(DEFAULT_ASSETS)
    for ext in root.findall("extension"):
        if ext.get("point") != "xbmc.addon.metadata":
            continue
        assets = ext.find("assets")
        if assets is None:
            continue
        for element in assets:
            if element.text and element.text.strip():
                found.add(element.text.strip().replace("\\", "/"))
    safe = []
    for rel in sorted(found):
        norm = posixpath.normpath(rel)
        if norm.startswith("..") or posixpath.isabs(norm):
            print(f"  !! {addon.id}: ignoring unsafe asset path {rel!r}", file=sys.stderr)
            continue
        safe.append(norm)
    return safe


def external_imports(addons: list[Addon]) -> dict[str, list[str]]:
    """{imported id: [add-ons importing it]} for every import that is neither virtual nor ours."""
    ours = {a.id for a in addons}
    wanted: dict[str, list[str]] = {}
    for addon in addons:
        root = ET.parse(os.path.join(addon.path, "addon.xml")).getroot()
        for imp in root.findall("./requires/import"):
            dep = imp.get("addon", "")
            if not dep or dep.startswith(VIRTUAL_PREFIXES) or dep in ours:
                continue
            wanted.setdefault(dep, []).append(addon.id + (" (optional)" if imp.get("optional") == "true" else ""))
    return dict(sorted(wanted.items()))


# --------------------------------------------------------------------------- build steps
def build_addon_files(addon: Addon, zips_dir: str) -> str:
    """Zip + artwork + changelog of one add-on into <zips_dir>/<id>/. Returns the zip path."""
    target = os.path.join(zips_dir, addon.id)
    os.makedirs(target, exist_ok=True)
    zip_path = build_addons.build_zip(addon, out_dir=target)

    for rel in asset_paths(addon):
        src = os.path.join(addon.path, *rel.split("/"))
        if os.path.isfile(src):
            dst = os.path.join(target, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)

    changelog = os.path.join(addon.path, "changelog.txt")
    if os.path.isfile(changelog):
        shutil.copyfile(changelog, os.path.join(target, f"changelog-{addon.version}.txt"))
    return zip_path


def write_index(out_dir: str, addons: list[Addon]) -> None:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<addons>"]
    for addon in addons:
        parts.append(addon_xml_fragment(addon))
    parts.append("</addons>")
    _write_text(os.path.join(out_dir, "addons.xml"), "\n".join(parts) + "\n")
    # Kodi trims whitespace; the bare hex digest is the most compatible form.
    _write_text(os.path.join(out_dir, "addons.xml.md5"), _md5(os.path.join(out_dir, "addons.xml")))


def write_config(out_dir: str, base_url: str) -> dict:
    cfg_dir = os.path.join(out_dir, "config")
    zip_path = build_config.build(out_dir=cfg_dir)
    rel = "config/" + os.path.basename(zip_path)
    info = {
        "schema": 1,
        "config_version": build_config.read_config_version(),
        "file": rel,
        "url": f"{base_url}/{rel}" if base_url else None,
        "size": os.path.getsize(zip_path),
        "sha256": _sha256(zip_path),
        "commit": os.environ.get("GITHUB_SHA", ""),
    }
    _write_text(os.path.join(cfg_dir, "config.json"), json.dumps(info, indent=2) + "\n")
    return info


def write_aliases(out_dir: str, addons: list[Addon]) -> list[str]:
    made = []
    by_id = {a.id: a for a in addons}
    for addon_id in STABLE_ALIASES:
        addon = by_id.get(addon_id)
        if addon is None:
            print(f"  note: {addon_id} is not in the repo yet -- no stable alias written")
            continue
        src = os.path.join(out_dir, "zips", addon_id, addon.zip_name)
        name = f"{addon_id}-latest.zip"
        shutil.copyfile(src, os.path.join(out_dir, name))
        made.append(name)
    return made


def write_landing(out_dir: str, base_url: str, aliases: list[str]) -> None:
    """index.html. Kodi's HTTP file browser follows plain <a href> links, so the zips are linked directly."""
    links = "\n".join(f'    <li><a href="{html.escape(n)}">{html.escape(n)}</a></li>' for n in aliases)
    shown = html.escape(base_url + "/") if base_url else "(this address)"
    page = f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kodi POV IL repository</title>
</head>
<body>
  <h1>Kodi POV IL</h1>
  <p>Source URL / כתובת המקור: <code>{shown}</code></p>
  <ol>
    <li>Kodi &rarr; Settings &rarr; File manager &rarr; Add source &rarr; the URL above</li>
    <li>Add-ons &rarr; Install from zip file &rarr; choose the source &rarr; <b>repository.povil-latest.zip</b></li>
    <li>Add-ons &rarr; Install from repository &rarr; the POV IL repository &rarr; the Wizard</li>
  </ol>
  <p>Or install <b>plugin.program.kodipovilwizard-latest.zip</b> directly: it installs the repository on first start.</p>
  <ul>
{links}
  </ul>
</body>
</html>
"""
    _write_text(os.path.join(out_dir, "index.html"), page)
    _write_text(os.path.join(out_dir, ".nojekyll"), "")


# --------------------------------------------------------------------------- verification
def verify(out_dir: str, addons: list[Addon]) -> list[str]:
    """Re-read the finished site the way Kodi will. Returns a list of problems (empty = ok)."""
    problems: list[str] = []
    index_path = os.path.join(out_dir, "addons.xml")
    try:
        root = ET.parse(index_path).getroot()
    except (ET.ParseError, OSError) as exc:
        return [f"addons.xml unreadable: {exc}"]

    listed = {el.get("id"): el.get("version") for el in root.findall("addon")}
    expected = {a.id: a.version for a in addons}
    if listed != expected:
        problems.append(f"addons.xml lists {sorted(listed.items())} but expected {sorted(expected.items())}")

    try:
        with open(os.path.join(out_dir, "addons.xml.md5"), "r", encoding="ascii") as fh:
            recorded = fh.read().strip().split()[0]
        if recorded != _md5(index_path):
            problems.append("addons.xml.md5 does not match addons.xml")
    except (OSError, IndexError) as exc:
        problems.append(f"addons.xml.md5 unreadable: {exc}")

    for addon in addons:
        zip_path = os.path.join(out_dir, "zips", addon.id, addon.zip_name)
        if not os.path.isfile(zip_path):
            problems.append(f"{addon.id}: missing {addon.id}/{addon.zip_name}")
            continue
        try:
            with zipfile.ZipFile(zip_path) as zf:
                bad = zf.testzip()
                if bad:
                    problems.append(f"{addon.id}: corrupt member {bad}")
                names = zf.namelist()
                stray = [n for n in names if not n.startswith(addon.id + "/")]
                if stray:
                    problems.append(f"{addon.id}: entries outside '{addon.id}/': {stray[:3]}")
                if f"{addon.id}/addon.xml" not in names:
                    problems.append(f"{addon.id}: zip has no {addon.id}/addon.xml")
                else:
                    inner = ET.fromstring(zf.read(f"{addon.id}/addon.xml"))
                    if inner.get("id") != addon.id or inner.get("version") != addon.version:
                        problems.append(f"{addon.id}: zip addon.xml says {inner.get('id')} {inner.get('version')}")
        except (zipfile.BadZipFile, ET.ParseError) as exc:
            problems.append(f"{addon.id}: zip unreadable: {exc}")

    if not os.path.isfile(os.path.join(out_dir, "config", "config.json")):
        problems.append("config/config.json missing")
    return problems


def _site_size(out_dir: str) -> tuple[int, list[tuple[int, str]]]:
    total, big = 0, []
    for dirpath, _dirs, files in os.walk(out_dir):
        for name in files:
            path = os.path.join(dirpath, name)
            size = os.path.getsize(path)
            total += size
            if size > BIG_FILE_WARN:
                big.append((size, os.path.relpath(path, out_dir)))
    return total, big


# --------------------------------------------------------------------------- main
def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Kodi repository site for GitHub Pages.")
    parser.add_argument("--out", default="site", help="output directory (wiped first). Default: site")
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", ""),
                        help="public site URL, e.g. https://owner.github.io/repo (used in config.json / index.html)")
    parser.add_argument("--exclude", action="append", default=[], metavar="ID",
                        help="add-on id to leave out (repeatable); the third-party repository add-ons are always left out")
    args = parser.parse_args()

    out_dir = os.path.abspath(args.out)
    base_url = args.base_url.strip().rstrip("/")
    if not base_url:
        print("  note: no --base-url given; config.json will carry url=null", file=sys.stderr)

    excluded = set(THIRD_PARTY_REPOS) | set(args.exclude)
    addons = [a for a in discover_addons() if a.id not in excluded]

    # Strictness that Kodi itself would punish silently at install time.
    seen: dict[str, str] = {}
    for addon in addons:
        if addon.dirname != addon.id:
            raise RepoError(f"folder '{addon.dirname}' != addon id '{addon.id}' (the zip's top folder must be the id)")
        if addon.id in seen:
            raise RepoError(f"duplicate add-on id {addon.id}")
        seen[addon.id] = addon.version
        if not _NUMERIC_VERSION_RE.match(addon.version):
            print(f"  warn: {addon.id} version '{addon.version}' is not purely numeric; "
                  f"Kodi's comparison of letter suffixes is unverified", file=sys.stderr)

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    print(f"Building {len(addons)} add-ons into {out_dir}/zips:")
    zips_dir = os.path.join(out_dir, "zips")
    for addon in addons:
        build_addon_files(addon, zips_dir)

    write_index(out_dir, addons)
    cfg = write_config(out_dir, base_url)
    aliases = write_aliases(out_dir, addons)
    write_landing(out_dir, base_url, aliases)

    problems = verify(out_dir, addons)
    if problems:
        print("\nVERIFY FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  !! {p}", file=sys.stderr)
        return 1

    total, big = _site_size(out_dir)
    print(f"\nOK: {len(addons)} add-ons, config {cfg['config_version']}, site {total / 1e6:.1f} MB")
    for size, rel in big:
        print(f"  warn: {rel} is {size / 1e6:.1f} MB")
    if total > SITE_WARN:
        print(f"  warn: site is above {SITE_WARN // (1024 * 1024)} MB; GitHub Pages stops at 1 GB", file=sys.stderr)

    missing = external_imports(addons)
    if missing:
        print("\nImports not provided by this repo (must come from Kodi's repo or an upstream index "
              "listed in repository.povil):")
        for dep, users in missing.items():
            print(f"  - {dep:<32} <- {', '.join(users)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RepoError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
