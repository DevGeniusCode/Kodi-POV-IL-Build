#!/usr/bin/env python3
"""Health check of every upstream index that repository.povil.upstream aggregates.

Usage::

    python .github/scripts/check_upstreams.py [--repo-xml path/to/addon.xml]

WHY IT EXISTS: Kodi merges all <dir> blocks of a repository add-on into one update. If ANY <dir> fails (checksum or
index unreachable, a bad digest when verify= is set) the WHOLE repository update fails (xbmc/addons/Repository.cpp,
FetchIfChanged). Our own add-ons live in a separate repository (repository.povil), so an upstream outage can no longer stop
them, but it still stops POV / Idan Plus / Umbrella ... from installing and updating: we want to know first.

A <dir> that points at OUR site under /upstream/ (the CI-filtered copy of a third-party index, see gen_repo.FILTERED_UPSTREAMS)
is checked leniently: before the first deploy it does not exist yet, so a fetch failure is only a warning. The third party's
own index is checked directly, and the filtered copy must hold nothing but the add-ons we asked for.

Per <dir> (the version range is ignored: every block is checked):
  FAIL  the checksum or the index cannot be fetched, or the index is not valid addons.xml
  FAIL  verify= is set and the digest does not match
  FAIL  the index carries an add-on id that is also one of OURS (Kodi installs the highest version, so an upstream
        skin.fentastic 100.x would replace our fork)
  FAIL  an add-on in REQUIRED is not offered by any block, or its zip URL does not answer
  WARN  the checksum file does not match the index (harmless to Kodi without verify=, but it can delay noticing updates)

Exit code 1 on any FAIL. Run daily by .github/workflows/upstream-health.yml and on every change of repository.povil.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from gen_repo import FILTERED_UPSTREAMS  # noqa: E402
from kodi_addons import REPO_ROOT, discover_addons  # noqa: E402

# What the build actually takes from upstream. Keep in step with provisioning/provisioner.py (PROVISION_IDS) and the optional add-ons.
REQUIRED = frozenset({
    "plugin.video.pov",
    "plugin.video.idanplus",
    "plugin.video.umbrella",
    "script.module.cocoscrapers",
    "plugin.video.otaku",
    "skin.arctic.fuse.3",
    "script.skinvariables",
    "script.module.acctmgr",
    "script.module.acctvwr",
})

UA = {"User-Agent": "Mozilla/5.0 (kodi-povil-upstream-check)"}


def fetch(url: str, ranged: bool = False, tries: int = 3):
    """(status, body). Retries transient failures; a 404 is final."""
    last = "no attempt"
    for attempt in range(tries):
        try:
            headers = dict(UA)
            if ranged:
                headers["Range"] = "bytes=0-0"
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as resp:
                return resp.status, (resp.read(1) if ranged else resp.read())
        except Exception as exc:  # noqa: BLE001 - report whatever the network says
            last = str(exc)[:80]
            if "404" in last:
                return 404, b""
            time.sleep(2 * (attempt + 1))
    return last, b""


def digest(kind: str, data: bytes) -> str:
    return hashlib.new(kind, data).hexdigest()


def parse_dirs(repo_xml: str):
    root = ET.parse(repo_xml).getroot()
    out = []
    for d in root.findall("./extension[@point='xbmc.addon.repository']/dir"):
        chk = d.find("checksum")
        out.append({
            "range": " ".join(f'{k}={d.get(k)}' for k in ("minversion", "maxversion") if d.get(k)) or "any",
            "info": (d.findtext("info") or "").strip(),
            "checksum": (chk.text or "").strip() if chk is not None else "",
            "verify": chk.get("verify") if chk is not None else None,
            "datadir": (d.findtext("datadir") or "").strip(),
        })
    return out


def own_upstream_prefix(repo_xml: str) -> str:
    """'<our site>/upstream/' derived from repository.povil's own <dir>, or '' when it cannot be read."""
    try:
        own = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(repo_xml))), "repository.povil", "addon.xml")
        info = (parse_dirs(own)[0]["info"]).lower()
        return info.rsplit("/repo/addons.xml", 1)[0] + "/upstream/"
    except Exception:  # noqa: BLE001
        return ""


def check_dir(block: dict, ours: set[str], own_upstream: str = ""):
    """Returns (fails, warns, {id: version}, datadir)."""
    fails, warns = [], []
    label = block["info"]
    lenient = bool(own_upstream) and label.lower().startswith(own_upstream)
    spec = next((s for s in FILTERED_UPSTREAMS if lenient and label.lower().endswith("/upstream/%s/addons.xml" % s["name"])), None)
    if lenient:
        probe, _ = fetch(label, tries=2)
        if probe != 200:
            return [], ["%s: not published yet (%s); the next deploy writes it" % (label, probe)], {}, block["datadir"]
    if not block["checksum"]:
        return [f"{label}: no <checksum> (Kodi never fetches such a <dir>)"], warns, {}, block["datadir"]

    status, sumdata = fetch(block["checksum"])
    if status != 200:
        return [f"{block['checksum']}: checksum unreachable ({status})"], warns, {}, block["datadir"]
    status, body = fetch(block["info"])
    if status != 200:
        return [f"{label}: index unreachable ({status})"], warns, {}, block["datadir"]

    recorded = sumdata.decode("ascii", "ignore").strip().split()[:1]
    recorded = recorded[0].lower() if recorded else ""
    text = body
    if label.endswith(".gz"):
        try:
            text = gzip.decompress(body)
        except OSError as exc:
            return [f"{label}: not valid gzip ({exc})"], warns, {}, block["datadir"]

    kind = block["verify"] or ("md5" if len(recorded) == 32 else "sha256" if len(recorded) == 64 else None)
    if kind and recorded != digest(kind, body):
        message = f"{label}: {kind} {recorded[:12]}... does not match the index ({digest(kind, body)[:12]}...)"
        (fails if block["verify"] else warns).append(message)

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return fails + [f"{label}: index is not valid XML ({exc})"], warns, {}, block["datadir"]
    found = {el.get("id"): el.get("version") for el in root.findall("addon") if el.get("id")}
    if spec is not None:
        extra = sorted(set(found) - set(spec["keep"]))
        if extra:
            fails.append("%s: the filtered copy still lists %s (the filter in gen_repo.py is broken)" % (label, ", ".join(extra)))
    for addon_id in sorted(set(found) & ours):
        fails.append(f"{label}: carries '{addon_id}' {found[addon_id]}, which is also OUR add-on id (collision)")
    return fails, warns, found, block["datadir"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the upstream indexes of repository.povil.")
    parser.add_argument("--repo-xml", default=os.path.join(REPO_ROOT, "repository.povil.upstream", "addon.xml"))
    args = parser.parse_args()

    ours = {a.id for a in discover_addons()}
    blocks = parse_dirs(args.repo_xml)
    own_upstream = own_upstream_prefix(args.repo_xml)
    print(f"Checking {len(blocks)} upstream <dir> block(s) of {args.repo_xml}")

    all_fails, all_warns = [], []
    offered: dict[str, tuple[str, str]] = {}      # id -> (version, datadir)
    with ThreadPoolExecutor(4) as pool:
        for block, (fails, warns, found, datadir) in zip(blocks, pool.map(lambda b: check_dir(b, ours, own_upstream), blocks)):
            state = "FAIL" if fails else ("warn" if warns else "ok")
            print(f"  [{state:>4}] {block['range']:<28} {len(found):>3} add-ons  {block['info']}")
            all_fails += fails
            all_warns += warns
            for addon_id, version in found.items():
                offered.setdefault(addon_id, (version, datadir))

    for spec in FILTERED_UPSTREAMS:
        status, body = fetch(spec["index"])
        if status != 200:
            all_fails.append("source index of %s unreachable (%s): %s" % (spec["name"], status, spec["index"]))
            continue
        try:
            source = {el.get("id"): el.get("version") for el in ET.fromstring(body).findall("addon") if el.get("id")}
        except ET.ParseError as exc:
            all_fails.append("source index of %s is not valid XML (%s)" % (spec["name"], exc))
            continue
        print("  source %-10s %d add-ons, keeping %s" % (spec["name"], len(source), ", ".join(spec["keep"])))
        for addon_id in spec["keep"]:
            if addon_id not in source:
                all_fails.append("source index of %s no longer lists %s" % (spec["name"], addon_id))
            else:
                offered.setdefault(addon_id, (source[addon_id], spec["datadir"]))

    for addon_id in sorted(REQUIRED):
        if addon_id not in offered:
            all_fails.append(f"required add-on '{addon_id}' is not offered by any upstream <dir>")
            continue
        version, datadir = offered[addon_id]
        base = datadir if datadir.endswith("/") else datadir + "/"
        url = f"{base}{addon_id}/{addon_id}-{version}.zip"
        status, _ = fetch(url, ranged=True, tries=4)
        if status not in (200, 206):
            all_fails.append(f"required zip does not answer ({status}): {url}")
        else:
            print(f"  required {addon_id:<30} {version:<14} zip ok")

    for line in all_warns:
        print(f"WARN  {line}")
    for line in all_fails:
        print(f"FAIL  {line}", file=sys.stderr)
    print(f"\n{len(all_fails)} failure(s), {len(all_warns)} warning(s)")
    return 1 if all_fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
