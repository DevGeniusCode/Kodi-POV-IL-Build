#!/usr/bin/env python3
"""Generate the complete Kodi repository site (what GitHub Pages serves).

Usage::

    python .github/scripts/gen_repo.py --out site --base-url https://<owner>.github.io/<repo>

Output layout (``--out``)::

    index.html                          landing page; its ONLY static <a href="*.zip"> is repository.povil-latest.zip (what Kodi's
                                        file browser lists); the Wizard zip is a JS download button, see write_landing
    .nojekyll
    repo/addons.xml                     every add-on's addon.xml, wrapped in <addons> (NOT at the site root, see INDEX_DIR)
    repo/addons.xml.sha256              hex sha256 of addons.xml (verified by Kodi: <checksum verify="sha256">)
    repo/addons.xml.md5                 hex md5 of addons.xml (for older tools; Kodi does not need it)
    repository.povil-latest.zip         stable-name copies for install instructions
    plugin.program.kodipovilwizard-latest.zip
    zips/<id>/<id>-<version>.zip        the <datadir>: Kodi downloads exactly this path
    zips/<id>/<id>-<version>.zip.sha256 verified by Kodi per zip (<hashes>sha256</hashes> in repository.povil)
    zips/<id>/icon.*, fanart.*, ...     artwork declared in <assets> (Kodi shows it in the repo browser)
    zips/<id>/changelog-<version>.txt
    upstream/<name>/addons.xml (+ .sha256)  a FILTERED copy of a third-party index (see FILTERED_UPSTREAMS); no zip is stored
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
import time
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
import build_addons  # noqa: E402
import build_config  # noqa: E402
from kodi_addons import Addon, discover_addons  # noqa: E402

# Copied to <site>/<id>-latest.zip (+ .sha256) so install instructions and the Wizard's first-run bootstrap can use a name that
# never changes. All of them MUST exist in the repo: the build fails otherwise.
STABLE_ALIASES = ("repository.povil", "repository.povil.upstream", "plugin.program.kodipovilwizard")

# index.html: Kodi's HTTP file browser lists every static <a href="*.zip"> of the page, so exactly ONE is allowed, the repository
# zip. The Wizard zip is offered by a script-driven button (Kodi's scraper does not run scripts) and by the README; the upstream
# repository zip is installed by the Wizard and linked nowhere.
LANDING_LINK_ID = "repository.povil"
LANDING_BUTTON_ID = "plugin.program.kodipovilwizard"

# The repository index (addons.xml + its checksums) lives in its OWN folder, never in the folder Kodi's file browser lists (the
# site root). Why: browsing an HTTP source makes Kodi keep that folder's listing (CDirectoryCache, DIR_CACHE_ONCE) and
# CFile::Open() then refuses, WITHOUT any network request, every file of that folder the listing does not contain. The listing is
# built from the <a href> links of index.html, so "addons.xml.sha256" at the root is "not there" and the repository update dies
# at once with "CRepository: failed read '.../addons.xml.sha256'" (no CCurlFile line in the log). A folder nobody browses is not
# in that cache. The same reason keeps zips/<id>/ and upstream/<name>/ out of the root.
INDEX_DIR = "repo"

# Files Kodi looks for next to the zips when <assets> does not name them.
DEFAULT_ASSETS = ("icon.png", "fanart.jpg")

VIRTUAL_PREFIXES = ("xbmc.", "kodi.")

# Third-party indexes that carry add-ons we need next to ids of OURS (Kodi installs the highest version of an id, so such an
# index would replace our forks). At build time the index is fetched, reduced to the add-ons in "keep", and published under
# upstream/<name>/addons.xml; repository.povil.upstream points its <info>/<checksum> there and its <datadir> at the third
# party's own zip server. Nothing of theirs is stored in this repository or on our site except those few <addon> entries.
#   zaxxon709: Account Manager Lite. Its index also lists skin.fentastic 100.x and script.fentastic.helper 100.x.
FILTERED_UPSTREAMS = (
    {
        "name": "zaxxon709",
        "index": "https://raw.githubusercontent.com/Zaxxon709/zaxxon/main/zips/addons.xml",
        "datadir": "https://raw.githubusercontent.com/Zaxxon709/zaxxon/main/zips/",
        "keep": ("script.module.acctmgr", "script.module.acctvwr"),
    },
)
FETCH_TRIES = 3
FETCH_TIMEOUT = 20
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


def _repository_dirs(addon: Addon) -> list[ET.Element]:
    root = ET.parse(os.path.join(addon.path, "addon.xml")).getroot()
    return root.findall("./extension[@point='xbmc.addon.repository']/dir")


def check_repositories(addons: list[Addon], base_url: str) -> None:
    """The two repository add-ons must keep their roles.

    repository.povil describes THIS site, exactly the way gen_repo.py publishes it, and has no other <dir>: Kodi fails a whole
    repository when any one <dir> fails, so nothing we do not control may live in it. repository.povil.upstream holds the
    third-party indexes and must never point back at this site.
    """
    by_id = {a.id: a for a in addons}
    own, upstream = by_id.get("repository.povil"), by_id.get("repository.povil.upstream")
    for repo in (own, upstream):
        if repo is not None and ET.parse(os.path.join(repo.path, "addon.xml")).getroot().find("requires") is not None:
            raise RepoError(f"{repo.id}: a repository add-on must not have a <requires> block "
                            "(Kodi 21: \"failed to install repository ... It has dependencies defined\")")
    if own is not None:
        dirs = _repository_dirs(own)
        if len(dirs) != 1:
            raise RepoError(f"repository.povil must have exactly one <dir> (has {len(dirs)}); "
                            "third-party indexes belong to repository.povil.upstream")
        if base_url:
            want = base_url.lower() + "/"
            d = dirs[0]
            chk = d.find("checksum")
            problems = []
            index_url = want + INDEX_DIR + "/addons.xml"
            if (d.findtext("info") or "").strip().lower() != index_url:
                problems.append("<info> must be " + index_url)
            if chk is None or (chk.text or "").strip().lower() != index_url + ".sha256" or chk.get("verify") != "sha256":
                problems.append('<checksum verify="sha256"> must be ' + index_url + ".sha256")
            if (d.findtext("datadir") or "").strip().lower() != want + "zips/":
                problems.append("<datadir> must be " + want + "zips/")
            if (d.findtext("hashes") or "").strip().lower() != "sha256":
                problems.append("<hashes>sha256</hashes> is missing")
            if problems:
                raise RepoError("repository.povil's <dir>: " + "; ".join(problems))
    if upstream is not None and base_url:
        own, allowed = base_url.lower() + "/", base_url.lower() + "/upstream/"
        dirs = _repository_dirs(upstream)
        for d in dirs:
            info = (d.findtext("info") or "").strip().lower()
            if info.startswith(own) and not info.startswith(allowed):
                raise RepoError("repository.povil.upstream has a <dir> that points at our own index; that belongs in repository.povil")
        for spec in FILTERED_UPSTREAMS:
            want = f"{base_url}/upstream/{spec['name']}/addons.xml".lower()
            match = [d for d in dirs if (d.findtext("info") or "").strip().lower() == want]
            if len(match) != 1:
                raise RepoError(f"repository.povil.upstream must have exactly one <dir> whose <info> is {want}")
            d = match[0]
            chk = d.find("checksum")
            problems = []
            if chk is None or (chk.text or "").strip().lower() != want + ".sha256" or chk.get("verify") != "sha256":
                problems.append('<checksum verify="sha256"> must be ' + want + ".sha256")
            if (d.findtext("datadir") or "").strip() != spec["datadir"]:
                problems.append("<datadir> must be " + spec["datadir"])
            if problems:
                raise RepoError(f"repository.povil.upstream's <dir> for {spec['name']}: " + "; ".join(problems))


def _fetch(url: str, tries: int = FETCH_TRIES) -> bytes | None:
    """The body of url, or None after `tries` failed attempts (never raises)."""
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "kodi-povil-gen-repo"})
            with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - any network error means "try again, then fall back"
            print(f"  note: fetching {url} failed ({str(exc)[:70]}), attempt {attempt + 1}/{tries}", file=sys.stderr)
            time.sleep(1 + attempt)
    return None


def filter_index(raw: bytes, keep: tuple[str, ...]) -> str:
    """An addons.xml document reduced to the <addon> entries named in `keep` (in that order). Raises RepoError if one is missing."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RepoError(f"index is not valid XML: {exc}")
    found = {el.get("id"): el for el in root.findall("addon") if el.get("id") in keep}
    missing = [k for k in keep if k not in found]
    if missing:
        raise RepoError(f"index does not list {', '.join(missing)}")
    body = "\n".join(ET.tostring(found[k], encoding="unicode").strip() for k in keep)
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n' + body + "\n</addons>\n"


EMPTY_INDEX = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons />\n'


def write_filtered_upstreams(out_dir: str, base_url: str) -> list[tuple[str, str]]:
    """Publish upstream/<name>/addons.xml (+ .sha256, .md5) for every FILTERED_UPSTREAMS entry. Returns [(name, source)].

    A temporary outage of the third party must not break our build, so the sources are tried in this order:
      1. "upstream": the third party's live index;
      2. "cache": the filtered copy already published on our site (the previous deploy);
      3. "empty": an index with no add-ons (first deploy only; the add-ons are simply not offered until the next run).
    """
    results = []
    for spec in FILTERED_UPSTREAMS:
        name = spec["name"]
        text, source = None, "empty"
        candidates = [("upstream", spec["index"])]
        if base_url:
            candidates.append(("cache", f"{base_url}/upstream/{name}/addons.xml"))
        for label, url in candidates:
            raw = _fetch(url, tries=FETCH_TRIES if label == "upstream" else 2)
            if raw is None:
                continue
            try:
                text, source = filter_index(raw, spec["keep"]), label
                break
            except RepoError as exc:
                print(f"  warn: {label} index of {name} is unusable ({exc})", file=sys.stderr)
        if text is None:
            text = EMPTY_INDEX
            print(f"  warn: no usable index for {name}; publishing an EMPTY one (its add-ons are not offered until a later deploy)",
                  file=sys.stderr)
        target = os.path.join(out_dir, "upstream", name, "addons.xml")
        _write_text(target, text)
        _write_text(target + ".sha256", _sha256(target))
        _write_text(target + ".md5", _md5(target))
        results.append((name, source))
    return results


# --------------------------------------------------------------------------- build steps
def build_addon_files(addon: Addon, zips_dir: str) -> str:
    """Zip + artwork + changelog of one add-on into <zips_dir>/<id>/. Returns the zip path."""
    target = os.path.join(zips_dir, addon.id)
    os.makedirs(target, exist_ok=True)
    zip_path = build_addons.build_zip(addon, out_dir=target)
    _write_text(zip_path + ".sha256", _sha256(zip_path))

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
    index_path = os.path.join(out_dir, INDEX_DIR, "addons.xml")
    _write_text(index_path, "\n".join(parts) + "\n")
    # Kodi trims whitespace; the bare hex digest is the most compatible form.
    _write_text(index_path + ".md5", _md5(index_path))
    _write_text(index_path + ".sha256", _sha256(index_path))


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
            raise RepoError(f"{addon_id} is not in the repo; the site needs it (install path and first-run bootstrap)")
        src = os.path.join(out_dir, "zips", addon_id, addon.zip_name)
        name = f"{addon_id}-latest.zip"
        dst = os.path.join(out_dir, name)
        shutil.copyfile(src, dst)
        _write_text(dst + ".sha256", _sha256(dst))
        made.append(name)
    return made


def write_landing(out_dir: str, base_url: str, aliases: list[str]) -> None:
    """index.html.

    Kodi's HTTP file browser lists every static <a href="*.zip"> of this page, so the page carries exactly ONE: the repository
    zip. The Wizard zip is a <button> whose script sets location.href (Kodi's scraper does not run scripts and only looks at
    <a href>); the upstream repository zip is not offered at all (the Wizard installs it). Both stay deployed at their root
    URLs. verify() re-reads this page and fails the build on a second static zip link.
    """
    link, button = f"{LANDING_LINK_ID}-latest.zip", f"{LANDING_BUTTON_ID}-latest.zip"
    for needed in (link, button):
        if needed not in aliases:
            raise RepoError(f"{needed} was not written; the landing page needs it")
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
    <li>Add-ons &rarr; Install from zip file &rarr; choose the source &rarr; <b>{link}</b></li>
    <li>Add-ons &rarr; Install from repository &rarr; the POV IL repository &rarr; the Wizard</li>
  </ol>
  <ul>
    <li><a href="{link}">{link}</a></li>
  </ul>
  <p>Wizard zip for a normal browser (download it, then Kodi &rarr; Install from zip file &rarr; the downloaded file; do not
  install it from a remote source, Kodi would need one connection per file):</p>
  <p><button type="button" id="wizard-zip" data-file="{button}">Download {button}</button></p>
  <script>
    document.getElementById("wizard-zip").addEventListener("click", function () {{
      window.location.href = this.getAttribute("data-file");
    }});
  </script>
</body>
</html>
"""
    _write_text(os.path.join(out_dir, "index.html"), page)
    _write_text(os.path.join(out_dir, ".nojekyll"), "")


# --------------------------------------------------------------------------- verification
def verify(out_dir: str, addons: list[Addon]) -> list[str]:
    """Re-read the finished site the way Kodi will. Returns a list of problems (empty = ok)."""
    problems: list[str] = []
    index_path = os.path.join(out_dir, INDEX_DIR, "addons.xml")
    for stray in ("addons.xml", "addons.xml.sha256", "addons.xml.md5"):
        if os.path.exists(os.path.join(out_dir, stray)):
            problems.append(f"{stray} must not sit in the site root (see INDEX_DIR): Kodi's directory cache hides it after a browse")
    try:
        root = ET.parse(index_path).getroot()
    except (ET.ParseError, OSError) as exc:
        return problems + [f"{INDEX_DIR}/addons.xml unreadable: {exc}"]

    listed = {el.get("id"): el.get("version") for el in root.findall("addon")}
    expected = {a.id: a.version for a in addons}
    if listed != expected:
        problems.append(f"addons.xml lists {sorted(listed.items())} but expected {sorted(expected.items())}")

    try:
        with open(index_path + ".md5", "r", encoding="ascii") as fh:
            recorded = fh.read().strip().split()[0]
        if recorded != _md5(index_path):
            problems.append("addons.xml.md5 does not match addons.xml")
    except (OSError, IndexError) as exc:
        problems.append(f"addons.xml.md5 unreadable: {exc}")

    try:
        with open(index_path + ".sha256", "r", encoding="ascii") as fh:
            if fh.read().strip().split()[0] != _sha256(index_path):
                problems.append("addons.xml.sha256 does not match addons.xml")
    except (OSError, IndexError) as exc:
        problems.append(f"addons.xml.sha256 unreadable: {exc}")

    for addon in addons:
        zip_path = os.path.join(out_dir, "zips", addon.id, addon.zip_name)
        if not os.path.isfile(zip_path):
            problems.append(f"{addon.id}: missing {addon.id}/{addon.zip_name}")
            continue
        try:
            with open(zip_path + ".sha256", "r", encoding="ascii") as fh:
                if fh.read().strip().split()[0] != _sha256(zip_path):
                    problems.append(f"{addon.id}: {addon.zip_name}.sha256 does not match the zip")
        except (OSError, IndexError) as exc:
            problems.append(f"{addon.id}: {addon.zip_name}.sha256 unreadable: {exc}")
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

    for spec in FILTERED_UPSTREAMS:
        target = os.path.join(out_dir, "upstream", spec["name"], "addons.xml")
        try:
            ET.parse(target)
            with open(target + ".sha256", "r", encoding="ascii") as fh:
                if fh.read().strip().split()[0] != _sha256(target):
                    problems.append(f"upstream/{spec['name']}/addons.xml.sha256 does not match")
        except (OSError, ET.ParseError, IndexError) as exc:
            problems.append(f"upstream/{spec['name']}/addons.xml unusable: {exc}")

    if not os.path.isfile(os.path.join(out_dir, "config", "config.json")):
        problems.append("config/config.json missing")

    try:
        with open(os.path.join(out_dir, "index.html"), "r", encoding="utf-8") as fh:
            page = fh.read()
        zip_links = re.findall(r"<a\b[^>]*?\bhref\s*=\s*[\"']?([^\"'\s>]*\.zip)\b", page, flags=re.IGNORECASE)
        if zip_links != [f"{LANDING_LINK_ID}-latest.zip"]:
            problems.append(f"index.html must have exactly one static zip link, {LANDING_LINK_ID}-latest.zip (found {zip_links})")
    except OSError as exc:
        problems.append(f"index.html unreadable: {exc}")
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
                        help="add-on id to leave out (repeatable)")
    args = parser.parse_args()

    out_dir = os.path.abspath(args.out)
    base_url = args.base_url.strip().rstrip("/")
    if not base_url:
        print("  note: no --base-url given; config.json will carry url=null", file=sys.stderr)

    excluded = set(args.exclude)
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

    check_repositories(addons, base_url)

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    print(f"Building {len(addons)} add-ons into {out_dir}/zips:")
    zips_dir = os.path.join(out_dir, "zips")
    for addon in addons:
        build_addon_files(addon, zips_dir)

    write_index(out_dir, addons)
    filtered = write_filtered_upstreams(out_dir, base_url)
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
    for name, source in filtered:
        print(f"  filtered upstream index {name}: {source}" + ("" if source == "upstream" else "   <-- NOT the live upstream"))
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
