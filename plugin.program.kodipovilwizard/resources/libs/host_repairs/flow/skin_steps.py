# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""Skin and player-UI repair steps (NOX, FENtastic, Estuary dialogs and buttons).

Moved verbatim from service.subtitles.kodipovilai/service.py (Phase 3, step 3); only the import prefix differs.
"""
import os

try:
    import xbmc
except ImportError:
    xbmc = None

from resources.libs.host_repairs.flow.common import ADDON_ID, _other_addon_version, _walk_all, _without


def _tile_reload_worker():
    """Do ONE skin reload so freshly-cache-dropped tiles re-cache from disk. The
    home focus is snapshotted + restored (via pov_reload) so the menu doesn't snap
    to the first tile if the user was already navigating. No-op while playing."""
    try:
        import xbmc
        if xbmc.getCondVisibility('Player.HasMedia'):
            return
        # Wait out any POV cycle FIRST. This function has always imported
        # pov_reload -- for the focus snapshot -- and never asked it the one
        # question that matters: rebuilding every window while POV cannot be
        # constructed is what breaks the home screen. It applies to every skin,
        # since unlike the other reload sites this one has no skin guard at all.
        settled, saved = True, None
        try:
            from resources.libs.host_repairs import pov_reload
            settled = pov_reload.wait_until_settled()
            if settled:
                saved = pov_reload._capture_home_focus()
        except Exception:
            settled, saved = True, None
        if not settled:
            return
        xbmc.executebuiltin('ReloadSkin()')
        try:
            xbmc.sleep(1200)
        except Exception:
            pass
        if saved:
            try:
                from resources.libs.host_repairs import pov_reload
                pov_reload._restore_home_focus(saved)
            except Exception:
                pass
    except Exception:
        pass


def _maybe_patch_skin_dialog_subtitles():
    """Self-healing patch of the ACTIVE skin's DialogSubtitles.xml
    so the subtitle-picker dialog HEADER prefers our window property
    `subs.player_filename` (set by POV's source picker AND/OR our
    own SubsFilenamePublisher player monitor) over the built-in
    `Player.Filename`. Without this, the header shows the UUID
    basename of TorBox CDN URLs even when our property is set --
    because Kodi's DialogSubtitles XML resolves Player.Filename
    directly from the player URL, not from any addon-settable
    state. Patching the skin's XML makes the header read our
    property first.

    This patcher auto-detects the active skin via xbmc.getSkinDir()
    and works against FENtastic, Arctic Zephyr (any variant),
    Estuary, Aeon Nox -- any skin whose DialogSubtitles.xml has a
    `<control type="label">…$INFO[Player.Filename]…</control>`
    element. Users who chose a non-FENtastic skin previously saw
    the UUID gibberish in the header even on the latest addon
    version because the old FENtastic-only patcher returned
    'no_file' for them.

    Self-migrates the old FENtastic-specific v1 inject so users
    upgrading don't end up with stale v1 dual-control blocks
    sitting next to the new v2 ones."""
    try:
        from resources.libs.host_repairs import skin_dialog_subtitles_patcher, \
            kodi_utils
    except Exception:
        return
    try:
        status = skin_dialog_subtitles_patcher.ensure_patched()
        if status == 'patched':
            kodi_utils.log(
                'skin_dialog_subtitles_patcher: dialog header now '
                'prefers subs.player_filename', level='INFO')
        elif status in ('unmatched', 'write_failed', 'read_failed',
                        'no_target'):
            kodi_utils.log(
                'skin_dialog_subtitles_patcher: ' + status,
                level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log(
                'skin_dialog_subtitles_patcher failed: '
                '{0}'.format(e), level='WARNING')
        except Exception:
            pass


def _maybe_patch_nox_change_source():
    """Add a 'החלף מקור' (change source) button to the NOX skin's player OSD
    (skin.povil.nox/xml/VideoOSD.xml). NOX shipped without one, so a bad source
    mid-playback left users stuck with no way to pick another. No-op when NOX
    isn't installed. Marker-gated + XML-parse-checked so it can never corrupt
    the skin / black-screen the player."""
    try:
        from resources.libs.host_repairs import nox_change_source_patcher, kodi_utils
    except Exception:
        return
    try:
        status = nox_change_source_patcher.ensure_patched()
        if status == 'patched':
            kodi_utils.log(
                'nox_change_source_patcher: change-source button added to '
                'NOX OSD', level='INFO')
            _maybe_reload_nox_skin()
        elif status in ('unmatched', 'parse_failed', 'write_failed',
                        'read_failed'):
            kodi_utils.log('nox_change_source_patcher: ' + status,
                           level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log('nox_change_source_patcher failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_patch_nox_osd_collision():
    """Shrink NOX's right-side OSD buttons back to their pre-change-source total
    width, so adding "החלף מקור" no longer pushes "הפרק הבא" left into the central
    play controls (the overlap that only showed during playback). No-op when NOX
    isn't installed or the buttons aren't at their known original widths. Marker-
    gated + XML-parse-checked."""
    try:
        from resources.libs.host_repairs import nox_osd_collision_patcher, kodi_utils
    except Exception:
        return
    try:
        status = nox_osd_collision_patcher.ensure_patched()
        if status == 'patched':
            kodi_utils.log(
                'nox_osd_collision_patcher: NOX OSD buttons re-sized so '
                '"הפרק הבא" no longer collides with the play controls',
                level='INFO')
            _maybe_reload_nox_skin()
        elif status in ('parse_failed', 'write_failed', 'read_failed'):
            kodi_utils.log('nox_osd_collision_patcher: ' + status,
                           level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log('nox_osd_collision_patcher failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_patch_nox_next_episode():
    """Repoint NOX's fullscreen-OSD "next episode" button from POV's dropped
    play_media&next=1 call to POV's working next-episode list. No-op when NOX
    isn't installed or the button was already repointed / changed upstream."""
    try:
        from resources.libs.host_repairs import nox_next_episode_patcher, kodi_utils
    except Exception:
        return
    try:
        status = nox_next_episode_patcher.ensure_patched()
        if status == 'patched':
            kodi_utils.log(
                'nox_next_episode_patcher: OSD next-episode button repointed',
                level='INFO')
            _maybe_reload_nox_skin()
        elif status in ('write_failed', 'read_failed', 'unmatched'):
            kodi_utils.log('nox_next_episode_patcher: ' + status,
                           level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log('nox_next_episode_patcher failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_reload_nox_skin():
    """Skin XML is read at skin load, so a freshly-applied NOX OSD patch only
    shows after a reload. Reload once when NOX is the active skin. Otherwise the
    button simply appears on the next Kodi restart."""
    try:
        import xbmc
    except Exception:
        return
    try:
        if xbmc.getSkinDir() != 'skin.povil.nox':
            return
        xbmc.executebuiltin('ReloadSkin()')
    except Exception:
        pass


def _maybe_patch_choose_subs_buttons():
    """Wire the player's subtitle button to MoranSubs's chooser window:
    rewire FENtastic + Estuary (they pointed at the disabled DarkSubs) and
    rewire NOX's existing subtitles button (id 70046, which only opened
    ActivateWindow(2118)). NOX is rewired -- NOT given a new button -- because an
    added button widened the right-aligned OSD group and pushed "החלף מקור" into
    the play controls. All skin-gated, XML-parse-checked, self-healing. A reload
    is done only when the patched skin is the active one."""
    # FENtastic + Estuary: rewire the existing DarkSubs button to our chooser.
    try:
        from resources.libs.host_repairs import choose_subs_rewire_patcher, kodi_utils
        import xbmc
        results = choose_subs_rewire_patcher.ensure_patched()
        active = ''
        try:
            active = xbmc.getSkinDir()
        except Exception:
            active = ''
        # Keys are "skin_id:file"; reload once if the ACTIVE skin got patched.
        active_patched = False
        for key, status in (results or {}).items():
            skin_id = key.split(':', 1)[0]
            if status == 'patched':
                kodi_utils.log('choose_subs_rewire_patcher: rewired {0} to '
                               'MoranSubs chooser'.format(key), level='INFO')
                if skin_id == active:
                    active_patched = True
            elif status in ('parse_failed', 'write_failed', 'read_failed'):
                kodi_utils.log('choose_subs_rewire_patcher: {0} -> {1}'.format(
                    key, status), level='WARNING')
        if active_patched:
            try:
                _reload_skin_if_safe()
            except Exception:
                pass
    except Exception as e:
        try:
            kodi_utils.log('choose_subs_rewire_patcher failed: {0}'
                           .format(e), level='WARNING')
        except Exception:
            pass
    # NOX: rewire the existing subtitles button (no new button -> no collision).
    try:
        from resources.libs.host_repairs import nox_choose_subs_patcher, kodi_utils
        status = nox_choose_subs_patcher.ensure_patched()
        if status == 'patched':
            kodi_utils.log('nox_choose_subs_patcher: NOX subtitles button '
                           'rewired to MoranSubs chooser', level='INFO')
            _maybe_reload_nox_skin()
        elif status in ('unmatched', 'parse_failed', 'write_failed',
                        'read_failed'):
            kodi_utils.log('nox_choose_subs_patcher: ' + status,
                           level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log('nox_choose_subs_patcher failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_patch_change_source_pause():
    """Make the player's "החלף מקור" (change source) button pause the video
    before opening the source-selection screen -- it used to pause but started
    playing through in the background. Injects a Player.Playing-gated
    PlayerControl(Play) onclick before the existing change-source onclick in
    NOX, Estuary, and FENtastic. Marker-gated, XML-parse-checked, self-healing.
    Must run AFTER the change-source button patchers so Estuary's inserted
    button exists. Reloads only the active skin if it was patched."""
    try:
        from resources.libs.host_repairs import change_source_pause_patcher, kodi_utils
        import xbmc
        results = change_source_pause_patcher.ensure_patched()
        active = ''
        try:
            active = xbmc.getSkinDir()
        except Exception:
            active = ''
        # Keys are "skin_id:file"; reload once if the ACTIVE skin got patched.
        active_patched = False
        for key, status in (results or {}).items():
            skin_id = key.split(':', 1)[0]
            if status == 'patched':
                kodi_utils.log('change_source_pause_patcher: {0} change-source '
                               'now pauses before opening sources'.format(
                                   key), level='INFO')
                if skin_id == active:
                    active_patched = True
            elif status in ('parse_failed', 'write_failed', 'read_failed'):
                kodi_utils.log('change_source_pause_patcher: {0} -> {1}'.format(
                    key, status), level='WARNING')
        if active_patched:
            try:
                _reload_skin_if_safe()
            except Exception:
                pass
    except Exception as e:
        try:
            kodi_utils.log('change_source_pause_patcher failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _reload_skin_if_safe():
    """ReloadSkin() -- but NEVER while the home window is showing. All our
    reloads here refresh player-OSD XML (VideoOSD.xml on Estuary/FENtastic/NOX)
    after a fresh patch. ReloadSkin() rebuilds every window, and on Estuary/
    FENtastic the home menu is a fixedlist (defaultcontrol 9000, focusposition=0)
    that then snaps focus to the FIRST tile -- the "home jumps to tile 1 after an
    update" bug. Kodi loads VideoOSD.xml fresh on the next OSD open regardless, so
    skipping the reload while on home costs nothing and removes the focus jump.
    (If we're not on home when a patch lands, reload normally.)"""
    try:
        import xbmc
        if xbmc.getCondVisibility('Window.IsVisible(home)'):
            return
        # Not while POV is being cycled: ReloadSkin() rebuilds every window,
        # and any POV-backed one raises "Unknown addon id" until the cycle
        # finishes. Skipping outright is fine here -- unlike the widget
        # patcher's reload, this one only refreshes player-OSD XML, which Kodi
        # re-reads on the next OSD open anyway.
        cycling = False
        try:
            from resources.libs.host_repairs import pov_reload
            cycling = pov_reload.is_cycling()
        except Exception:
            cycling = False
        if cycling:
            return
        xbmc.executebuiltin("ReloadSkin()")
    except Exception:
        pass


def _maybe_patch_skin_dialog_subtitles_rows():
    """Self-healing patch of the ACTIVE skin's DialogSubtitles.xml
    so the per-row layout in the subtitle picker is tall enough for
    long release names to display both wrapped lines without
    clipping. Idempotent (marker-gated). Bumps itemlayout +
    focusedlayout heights by +40 px and any inner textbox control
    referencing $INFO[ListItem.Label2] by the same."""
    try:
        from resources.libs.host_repairs import (
            skin_dialog_subtitles_row_patcher, kodi_utils)
    except Exception:
        return
    try:
        status = skin_dialog_subtitles_row_patcher.ensure_patched()
        if status == 'patched':
            kodi_utils.log(
                'skin_dialog_subtitles_row_patcher: row height '
                'bumped so wrapped release names display fully',
                level='INFO')
        elif status in ('no_skin', 'no_file', 'no_target',
                        'already_patched'):
            pass  # quiet steady-state
        else:
            kodi_utils.log(
                'skin_dialog_subtitles_row_patcher: ' + status,
                level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log(
                'skin_dialog_subtitles_row_patcher failed: '
                '{0}'.format(e), level='WARNING')
        except Exception:
            pass


def _maybe_enable_osd_autoclose():
    """Turn on the skin's built-in OSD auto-close (4s) so the player bars hide
    after a few seconds instead of staying until Back.

    THIS USED TO NAME ONE SKIN. It gated on `getSkinDir() != 'skin.fentastic'`
    and returned otherwise, so it only ever reached FENtastic users -- while
    skin.povil.nox ships the identical feature (same `OSDAutoClose` /
    `OSDAutoCloseTime` settings, same Timers.xml `autoclosevideoosd` timer),
    also off by default. Every Nox user has had the bar stay up since the
    feature shipped, and that is what the report was.

    So it detects the CAPABILITY instead of matching a name: if the active
    skin's own XML declares OSDAutoClose, it supports it. AF3 and Estuary do
    not and are skipped without a list saying so.

    Seeding is recorded IN THE SKIN's settings, not ours. That matters both
    ways: the mark is per-skin for free, so switching to a skin that has never
    been seeded seeds it; and if a skin is reinstalled or its settings are
    reset, our mark disappears with them and the default is re-seeded. A
    deliberate opt-out AFTER seeding is left alone, because the mark is still
    there. An add-on-side marker could do neither -- which is how one lost
    write became permanent.

    The one population the skin-side mark cannot describe is the users the OLD
    migration already reached: FENtastic users carrying `_fen_osd_autoclose_v1`
    have been seeded, but on the add-on side, where the new code cannot see it.
    Treating them as unseeded would re-enable the setting for anyone who turned
    it back off on purpose, so that marker is read once and converted into the
    skin-side mark WITHOUT rewriting the values. It is the same promise the old
    marker made, kept in the new place.

    The mark is a skin BOOL, and that is not a stylistic choice. Kodi keeps
    skin bools and skin strings in two separate maps (CSkinInfo::m_bools and
    m_strings, each with its own name->id table), and `Skin.HasSetting` is
    wired to the bool one. A mark written with Skin.SetString is invisible to
    it -- the guard would read false forever and this migration would re-force
    the values on EVERY boot, which is the opposite of leaving an opt-out
    alone. _maybe_default_nox_poster_rating pairs them correctly; so does this.
    """
    try:
        from resources.libs.host_repairs import kodi_utils
        import xbmc
        import xbmcvfs
    except Exception:
        return
    try:
        skin = xbmc.getSkinDir() or ''
        if not skin:
            return
        if xbmc.getCondVisibility('Skin.HasSetting(AISubsOsdSeeded)'):
            return  # already seeded for this skin; respect what it is now
        if (skin == 'skin.fentastic'
                and kodi_utils.get_setting('_fen_osd_autoclose_v1', '') == '1'):
            # Already seeded by the old add-on-side migration. Carry the mark
            # over and touch nothing: whatever the value is now is the user's.
            xbmc.executebuiltin('Skin.SetBool(AISubsOsdSeeded)')
            return
        # A skin that does NOT have the feature never gets a skin-side mark --
        # there is nothing to mark -- so without this it is re-scanned on every
        # single boot, forever. Remember the answer per skin VERSION, so a skin
        # update that adds the feature is still picked up (one scan per skin
        # release, not one per boot).
        stamp = '%s=%s' % (skin, _other_addon_version(skin))
        no_feature = (kodi_utils.get_setting('_osd_autoclose_nofeature', '')
                      or '').split(',')
        if stamp in no_feature:
            return
        # Both roots, the same pair pov_reload and the wizard already walk: a
        # skin shipped INSIDE Kodi lives under special://xbmc, not
        # special://home, and looking in one place only means such a skin can
        # never be detected on any boot. Estuary happens not to have the
        # feature, so today this costs nothing -- but "we never looked" and
        # "it isn't there" were the same answer, which is how the whole bug
        # started.
        roots = [xbmcvfs.translatePath(r + skin + '/')
                 for r in ('special://home/addons/', 'special://xbmc/addons/')]
        supports = False
        scanned = 0
        for base, dirs, files in _walk_all(roots):
            # A skin's art outweighs its XML by orders of magnitude and holds
            # none of it. Pruning these keeps the walk to the markup, which
            # matters because a skin WITHOUT the feature never gets a mark and
            # is therefore re-scanned on every single start.
            dirs[:] = [d for d in dirs if d.lower() not in
                       ('media', 'themes', 'fonts', 'backgrounds', 'extras',
                        'colors', 'sounds', '.git')]
            for fn in files:
                if not fn.endswith('.xml'):
                    continue
                try:
                    with open(os.path.join(base, fn), encoding='utf-8',
                              errors='replace') as fh:
                        scanned += 1
                        if 'OSDAutoClose' in fh.read():
                            supports = True
                            break
                except OSError:
                    continue
            if supports:
                break
        others = _without(no_feature, skin)
        if not supports:
            # Only cache a negative we actually MEASURED. Zero files read means
            # the walk found nothing to read -- a skin installed somewhere
            # neither root covers, or one we could not open -- not that the
            # skin lacks the feature. A cached "no" from an empty walk would
            # be permanent for that skin version; leaving it uncached only
            # costs another look on the next start.
            if scanned:
                # Capped, and a stale stamp for this same skin is dropped, so
                # a skin that is updated often does not fill the list with its
                # own past versions.
                kodi_utils.set_setting('_osd_autoclose_nofeature',
                                       ','.join(others[-9:] + [stamp]))
            return  # this skin has no such feature -- nothing to turn on
        if others != [s for s in no_feature if s]:
            # This skin was recorded as featureless and now HAS the feature --
            # a skin update added it. Drop the obsolete entry instead of
            # letting it age out: the cache holds ten, and stale entries push
            # live ones out, which costs the rescans the cache exists to
            # prevent.
            kodi_utils.set_setting('_osd_autoclose_nofeature',
                                   ','.join(others))
        xbmc.executebuiltin('Skin.SetBool(OSDAutoClose)')
        xbmc.executebuiltin('Skin.SetString(OSDAutoCloseTime,4)')
        # executebuiltin queues; the read below can otherwise race the write
        # and report a failure that did not happen. Same 150ms the NOX rating
        # rollout settled on next door.
        xbmc.sleep(150)
        # Only claim it once the skin actually reports the setting: a write
        # issued while the skin is still loading can be lost, and marking
        # regardless is what made a single lost write permanent.
        if not xbmc.getCondVisibility('Skin.HasSetting(OSDAutoClose)'):
            kodi_utils.log(
                'OSD auto-close: %s did not take the setting, will retry on '
                'the next start' % skin, level='WARNING')
            return
        xbmc.executebuiltin('Skin.SetBool(AISubsOsdSeeded)')
        kodi_utils.log('OSD auto-close enabled (4s) for %s' % skin,
                       level='INFO')
    except Exception as e:
        try:
            kodi_utils.log('OSD auto-close seeding failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_default_nox_poster_rating():
    """One-shot rollout: FORCE the NOX skin's rating/score circle ON for posters
    so the content score shows on artwork -- for EVERYONE, the first time NOX is
    the active skin after this update. The rating is considered an important
    default, so this run also re-enables it for users who had previously turned
    it off (it sets 'circle_rating' and clears the mutually-exclusive
    'circle_none' / 'circle_userrating'). Marker-gated by a FRESH version (v2),
    so it re-applies exactly once even for users who already passed the earlier
    v1 default -- and a later MANUAL opt-out AFTER this run sticks again (we
    never force it back on on subsequent startups).

    Skin settings can only be read/written for the ACTIVE skin (Skin.HasSetting
    / Skin.SetBool / Skin.Reset target whatever skin is loaded), so this no-ops
    on every startup until NOX is actually the active skin -- then it applies
    and marks itself done. We confirm the bool actually took before marking
    done, so a write that didn't persist is retried on a later startup. No-op
    for users who never run NOX (the marker is simply never set)."""
    try:
        import xbmc
        from resources.libs.host_repairs import kodi_utils
    except Exception:
        return
    try:
        if kodi_utils.get_setting('_nox_poster_rating_default_v2', '') == '1':
            return
        # Only meaningful while NOX is the active skin -- otherwise the
        # Skin.* condition/builtin would read/write the wrong skin. Try again
        # on a later startup (cheap, marker stays unset).
        if xbmc.getSkinDir() != 'skin.povil.nox':
            return
        # Force the rating circle ON (override a prior 'off'/'user rating'
        # choice this once). circle_rating / circle_userrating / circle_none
        # are mutually exclusive, so clear the other two and set rating.
        xbmc.executebuiltin('Skin.Reset(circle_none)')
        xbmc.executebuiltin('Skin.Reset(circle_userrating)')
        xbmc.executebuiltin('Skin.SetBool(circle_rating)')
        xbmc.sleep(150)
        # Only mark done once the setting is actually present, so a write that
        # failed to take is retried next startup instead of being lost.
        if xbmc.getCondVisibility('Skin.HasSetting(circle_rating)'):
            kodi_utils.set_setting('_nox_poster_rating_default_v2', '1')
            kodi_utils.log('NOX poster rating circle force-enabled for '
                           'everyone (rollout v2)', level='INFO')
    except Exception as e:
        try:
            kodi_utils.log('NOX poster rating default migration failed: {0}'
                           .format(e), level='WARNING')
        except Exception:
            pass


def _point_subtitle_button(engine_on):
    """Make the skins' player "Choose subtitles" button open the right thing.

    The build skins ship the button gated two ways:
      * Estuary / FENtastic(VideoOsd3): Skin.HasSetting(ChooseSubtitlesButtonOpensKodiWindow)
      * FENtastic(VideoOsd1):           Skin.String(subtitlesearch) == kodisubtitle|darksubs
    When the engine is ON, DarkSubs is disabled, so the DarkSubs branch is a
    dead button. Set both skin settings to the "Kodi native subtitle window"
    side (it runs MoranSubs as the default service). When OFF, restore DarkSubs.
    Only touches the ACTIVE skin; cheap; safe if the setting doesn't exist."""
    if xbmc is None:
        return
    # Write only what differs. Every Skin.Set*/Reset makes Kodi re-evaluate
    # the open windows, and the home screen then asks every widget again:
    # QA (NOX and FENtastic) loaded each home row twice at every start-up,
    # the second round right after this ran with nothing to change.
    try:
        has_kodi_window = xbmc.getCondVisibility(
            'Skin.HasSetting(ChooseSubtitlesButtonOpensKodiWindow)')
        search = xbmc.getInfoLabel('Skin.String(subtitlesearch)')
        if engine_on:
            if not has_kodi_window:
                xbmc.executebuiltin('Skin.SetBool(ChooseSubtitlesButtonOpensKodiWindow)')
            if search != 'kodisubtitle':
                xbmc.executebuiltin('Skin.SetString(subtitlesearch,kodisubtitle)')
        else:
            if has_kodi_window:
                xbmc.executebuiltin('Skin.Reset(ChooseSubtitlesButtonOpensKodiWindow)')
            if search != 'darksubs':
                xbmc.executebuiltin('Skin.SetString(subtitlesearch,darksubs)')
    except Exception:
        pass


def _maybe_default_fentastic_player():
    """Heal the FENtastic player choice ONLY when it's unset.

    The build ships a default __chooseplayer=__netflixplayer so a fresh install
    never lands on a "player with nothing" (an empty string matches no player
    include in the skin -> no controls). But the quickfix must NOT keep
    re-asserting that default, or it reverts the user's manual player choice on
    every update (reported: "I switch to the simple player and the next update
    puts me back on Netflix"). So we no longer ship the skin settings file in
    the quickfix; instead we set a valid default HERE only when the value is
    empty -- and never touch a value the user picked. FENtastic-only (the
    setting is a FENtastic skin string; other skins handle players themselves).
    Uses the skin API (not a file write) so it can't fight Kodi's in-memory
    skin-settings cache."""
    if xbmc is None:
        return
    try:
        if xbmc.getSkinDir() != 'skin.fentastic':
            return
        cur = (xbmc.getInfoLabel('Skin.String(__chooseplayer)') or '').strip()
        if cur:
            return  # user (or a prior default) already set one -> respect it
        xbmc.executebuiltin('Skin.SetString(__chooseplayer,__netflixplayer)')
        xbmc.log('[' + ADDON_ID + '] set default __chooseplayer (was empty)',
                 level=xbmc.LOGINFO)
    except Exception:
        pass
