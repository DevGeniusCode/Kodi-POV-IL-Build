# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The subtitle link: a URL-quoted JSON payload (the seven link types).

import json
import urllib.parse


def encode_link(payload):
    return urllib.parse.quote(json.dumps(payload, ensure_ascii=False))


def decode_link(link):
    try:
        return json.loads(urllib.parse.unquote(link))
    except (ValueError, TypeError):
        return None


def decode_link_safe(link):
    """decode_link that never raises: whatever else goes wrong (a link that is not a string, JSON nested beyond the
    recursion limit) reads as "no link". The reading of sync/oracles, which promises never to raise."""
    try:
        return decode_link(link)
    except Exception:
        return None
