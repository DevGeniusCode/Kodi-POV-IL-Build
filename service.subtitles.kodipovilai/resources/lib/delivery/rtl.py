# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The Hebrew RTL display copy of a downloaded subtitle (one of the two RTL renderers).

import os
from resources.lib.core import kodi
from resources.lib.ai_translation.repairs import postfix_cleanup, postfix_glyphs, postfix_leaks, postfix_rtl
from resources.lib.core.srt import timing
from resources.lib.ai_translation.repairs.leak_rescue import rescue_reasoning_leaks


# Only plain timed-text formats can be passed through srt.fix_rtl_punctuation
# line-by-line. ASS/SSA and MicroDVD SUB carry timing/style fields on the SAME
# line as the dialogue; wrapping the full line would corrupt their syntax.
_RTL_PLAIN_TEXT_EXTS = ('.srt', '.vtt')
_RTL_DISPLAY_TOKEN = '.povil-rtl'
_LOGICAL_SOURCE_MARKER = '.logical-v1'


def rtl_text(text, legacy_engine=False, glyphs=True, clamp=True, ai_output=False, source=None,
             source_lang='auto'):
    """The ONE text transformation behind the three renderers below (text in, text out, no file, no log).

    Steps, in this order:
      * only with ``ai_output`` -- text our own model wrote (the callers decide: see each wrapper): the reasoning-leak
        rescue, the leaked-Arabic strip, the source-echo strip, the foreign-letter / stray-Latin folds and the niqqud
        strip. Each of them DELETES or rewrites words, so none of them may touch a human subtitle;
      * ``glyphs`` -- the canonical spelling of a character (safe on any subtitle, whatever made it);
      * the RTL punctuation fix (``legacy_engine`` reverses the old engine's known punctuation shapes);
      * ``clamp`` -- the bound on cue durations, which never deletes anything.
    ``source`` / ``source_lang`` are what the caller has for a cue with no Hebrew answer of its own (Google, then the
    source line itself). Exceptions are not caught here: each wrapper keeps its own policy."""
    body = text
    # The Arabic strip repairs a leak from the AI's gender-reference prompt, so it runs ONLY on bytes that prompt could
    # have produced. Two kinds of file cannot have been: a Ktuvit row mirrored into the pool (a HUMAN Hebrew subtitle) and
    # a Google Translate fallback (no cast/gender mechanism at all). Either one quoting Arabic --
    # 'הוא אמר "אינשאללה" (إن شاء الله)' -- would come back with the quote deleted and empty brackets left behind.
    if ai_output:
        # Same provenance gate: only our model writes its working into a cue.
        body, _leaks = rescue_reasoning_leaks(body, source, source_lang)
        body = postfix_glyphs.strip_leaked_arabic(body)
    # Same provenance gate as the Arabic strip, for the same reason: dropping a cue's leading non-Hebrew lines is only
    # ever right for OUR model's output. A human subtitle that deliberately shows an original line above its
    # translation must keep it.
    if ai_output:
        body = postfix_leaks.strip_source_echo(body)
        # Same provenance gate again, and the payoff is the same as the timing repair's: the wrong-alphabet slip and the
        # stray niqqud are already in cached files and in the pool, where dedup by source hash means those entries will
        # never be re-translated. Repairing them on the way IN clears the whole existing backlog for anyone who updates,
        # without a single re-translation. Only our model's output can carry either -- a human Hebrew subtitle may use
        # niqqud deliberately, and it is not ours to take out. The translated speaker tag cannot be repaired here:
        # deciding one is a tag needs the SOURCE entry, which a cached file no longer has.
        body = postfix_glyphs.fold_foreign_in_hebrew_word(body)
        # ...and its Latin form, as the final pass does ("בהTחשב"); files cached or pooled before that fold get it on the
        # way in.
        body = postfix_glyphs.fold_stray_latin_letter(body)
        body = postfix_cleanup.strip_niqqud(body)
    # Unconditional (``glyphs``): this only rewrites a character as the canonical spelling of that same character, so it
    # is safe on any subtitle, whatever made it.
    if glyphs:
        body = postfix_glyphs.normalize_glyphs(body)
    fixed = postfix_rtl.fix_rtl_punctuation(body, legacy_engine=legacy_engine)
    if clamp:
        fixed = timing.clamp_cue_durations(fixed)
    return fixed


def source_path_for_delivery(path):
    """Map our private display-copy path back to the immutable source file.

    Pool hashing/sharing must always read and mark the source, never the copy
    carrying playback-only BiDi controls. Falls back to the input path.
    """
    try:
        root, ext = os.path.splitext(path or '')
        if root.endswith(_RTL_DISPLAY_TOKEN):
            source = root[:-len(_RTL_DISPLAY_TOKEN)] + ext
            if os.path.isfile(source):
                return source
    except Exception:
        pass
    return path


def is_logical_source(path):
    try:
        return os.path.isfile(path + _LOGICAL_SOURCE_MARKER)
    except Exception:
        return False


def mark_logical_source(path):
    """Mark a freshly downloaded file as pristine logical-order text.

    Existing unmarked v2 entries are known to predate this change and may carry
    the vendored engine's physical punctuation reversal. The tiny local marker
    prevents that compatibility inverse from being applied to new downloads.
    It causes no network traffic and is never uploaded.
    """
    try:
        marker = path + _LOGICAL_SOURCE_MARKER
        if not os.path.exists(marker):
            with open(marker, 'w', encoding='ascii') as f:
                f.write('1')
    except Exception:
        pass


def render_hebrew_rtl_copy(sub_file, legacy_engine=False):
    """Return a playback-only RTL copy while leaving ``sub_file`` untouched.

    The source bytes, source hash and ".shared" marker remain stable, so this
    repair cannot re-upload existing pool subtitles. Unsupported structured
    formats are deliberately returned verbatim rather than risking their
    timing/style syntax. Fail-open on every error.

    ``legacy_engine`` reverses only the old engine's known punctuation shapes;
    fresh logical downloads pass False.

    Deliberately does NOT run srt.clamp_cue_durations, unlike every other place
    the RTL fix is applied. That clamp exists for a failure this path cannot
    have: our own model mistyping a timecode it was told to copy verbatim.
    These bytes come from Ktuvit / OpenSubtitles / Wizdom and never touch
    Gemini, their timing is treated as MORE trustworthy elsewhere in the add-on
    (subsync uses a foreign-language human sub as the oracle it corrects AI
    timing against), and real-world third-party SRT formatting is far more
    varied than Gemini's output -- so silently re-timing it is a risk taken for
    no matching benefit. If a stuck-cue report ever arrives against an engine
    download, this is the considered decision to revisit, not an oversight.
    """
    try:
        if os.path.splitext(sub_file or '')[1].lower() \
                not in _RTL_PLAIN_TEXT_EXTS:
            return sub_file
        with open(sub_file, 'rb') as f:
            raw = f.read()
        # extract_sub.convert_to_utf normalizes engine downloads first. Decode
        # strictly here so an unexpected binary/legacy file is never rewritten
        # with replacement characters.
        text = raw.decode('utf-8-sig')
        fixed = rtl_text(text, legacy_engine=legacy_engine, glyphs=False, clamp=False)
        if fixed == text:
            return sub_file
        root, ext = os.path.splitext(sub_file)
        out = root + _RTL_DISPLAY_TOKEN + ext
        tmp = out + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8', newline='') as f:
                f.write(fixed)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, out)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return out
    except Exception as e:
        kodi.log('subs_engine_bridge: RTL display copy skipped: {0}'
                       .format(e), level='DEBUG')
        return sub_file


def reapply_rtl_fix_in_place(path, legacy_engine=False, ai_output=None,
                              source=None, source_lang='auto'):
    """Repair a cached translation in place: RTL punctuation AND cue timings.
    Catches up files that were cached before the current version's fixes were
    wired in. Idempotent: if the file is already clean, no write happens.

    Called on every cache hit and every pool-reuse in resolve(), so a returning
    user benefits from the latest fix without clearing cache or waiting for the
    next service.py startup migration.

    The TIMING repair matters most for content that is ALREADY out there. A
    translation whose cue was welded to the screen by a mistyped timestamp is
    cached locally and, worse, may have been contributed to the community pool
    -- where dedup by source hash means it will never be re-translated, so every
    future viewer of that title gets the frozen line. Repairing on the way IN
    fixes the whole existing backlog for anyone who updates, without rewriting a
    single pooled file, touching Telegram, or spending one extra request."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except Exception:
        return
    # ai_output=None means "work it out": the '.google' sidecar already marks a
    # Google translation, so the DEFAULT is self-gating and a caller cannot
    # forget. Only the pool path passes an explicit value, because provenance
    # there comes from the row's pool_kind rather than from a sidecar. (What
    # `ai_output` allows is rtl_text's provenance gate: see there.)
    if ai_output is None:
        ai_output = postfix_glyphs.may_carry_arabic_leak(path)
    fixed = rtl_text(content, legacy_engine=legacy_engine, ai_output=ai_output,
                     source=source, source_lang=source_lang)
    if fixed == content:
        return
    tmp = path + '.aitmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(fixed)
        os.replace(tmp, path)
        kodi.log(
            'cached subtitle repaired in place (RTL / cue timings): ' + path,
            level='INFO')
    except OSError:
        try: os.remove(tmp)
        except OSError: pass


def rtl_delivery_copy(path, legacy_engine='auto'):
    """Render a Hebrew local-file candidate without touching its source bytes.

    A file sitting next to the video is not necessarily healthy: it may be an
    earlier AI translation of ours, saved alongside the video rather than in
    the add-on cache, and therefore out of reach of the cache migration. So the
    cue clamp runs here too, and the "nothing changed" shortcut has to consider
    BOTH passes -- a clamp-only repair still needs a delivery copy written.
    """
    try:
        with open(path, 'rb') as f:
            raw = f.read()
        content = raw.decode('utf-8-sig')
        # arabic-strip: not-our-bytes -- a file sitting next to the video may be
        # an earlier AI translation of ours OR a human subtitle the user
        # downloaded themselves, and nothing here tells them apart: no pool_kind,
        # no '.google' sidecar, nothing. The cue clamp is a bound that never
        # deletes anything, so it still runs; the Arabic strip DELETES text, so
        # it does not.
        #
        # ACCEPTED RESIDUAL, stated plainly because it is not free: this
        # function's own docstring says the population INCLUDES our own AI
        # translations saved beside the video, precisely the ones the cache
        # migration can never reach. Such a file carrying the leak is now
        # unfixable by any path. That is a worse outcome than the engine-download
        # exemption, where the content can never be AI output by construction --
        # here the false-negative cost is real. It is still the right trade while
        # the alternative is deleting Arabic out of a human subtitle the user
        # chose; a signal that identifies our own output would let this be
        # revisited.
        # normalize_glyphs only, deliberately. The echo strip stays out for the
        # same reason the Arabic strip does: nothing here identifies the file as
        # ours, and a human subtitle that shows the original above its
        # translation is a legitimate thing to leave alone. Glyph folding is
        # not a judgement call -- it rewrites a character as itself.
        fixed = rtl_text(content, legacy_engine=legacy_engine)
        if fixed == content:
            return path
        import hashlib as _hrtl
        sid = _hrtl.sha1(
            (os.path.abspath(path) + '\0').encode('utf-8', 'replace')
            + raw).hexdigest()[:16]
        out = os.path.join(
            kodi.cache_dir(), 'local_{0}.he.srt'.format(sid))
        tmp = out + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(fixed)
        os.replace(tmp, out)
        return out
    except Exception as e:
        kodi.log(
            'RTL local delivery copy skipped: {0}'.format(e), level='DEBUG')
        return path
