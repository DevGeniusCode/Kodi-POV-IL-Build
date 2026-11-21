# -*- coding: utf-8 -*-
import xbmc, xbmcgui

# from modules.logger import logger

WIDGET_WINDOW_IDS = (10000, 11121)


def _grab(stack_id, expected_id=None):
    """A FRESH Window and its two controls, or None when the active window is not one of ours (or is not the expected one) any more.

    A Window / Control object wraps a raw pointer and a skin reload (every FENtastic search does one) deletes the custom windows:
    a call on a stale object does not raise, it crashes Kodi. So nothing is kept across a wait: the caller asks again after each
    waitForAbort."""
    window_id = xbmcgui.getCurrentWindowId()
    if window_id not in WIDGET_WINDOW_IDS or (expected_id is not None and window_id != expected_id):
        return None
    try:
        window = xbmcgui.Window(window_id)
        stack_control = window.getControl(int(stack_id))
        stack_label_control = (
            window.getControl(int(stack_id + "666"))
            or window.getControl(int(stack_id + "667"))
            or window.getControl(int(stack_id + "668"))
            or window.getControl(int(stack_id + "669"))
            or window.getControl(int(stack_id + "670"))
            or window.getControl(int(stack_id + "671"))
            or window.getControl(int(stack_id + "672"))
        )
    except Exception:
        return None
    return window_id, window, stack_control, stack_label_control


def widget_monitor(list_id):
    if len(list_id) != 5:
        return
    monitor = xbmc.Monitor()
    try:
        delay = float(xbmc.getInfoLabel("Skin.String(category_widget_delay)")) / 1000
    except:
        delay = 0.75
    display_delay = (
        xbmc.getInfoLabel("Skin.HasSetting(category_widget_display_delay)") == "True"
    )
    stack_id = list_id + "1"
    while not monitor.abortRequested():
        grabbed = _grab(stack_id)
        if grabbed is None:
            break
        window_id = grabbed[0]
        monitor.waitForAbort(0.25)
        grabbed = _grab(stack_id, window_id)
        if grabbed is None:
            break
        _, window, stack_control, stack_label_control = grabbed
        if list_id != str(window.getFocusId()):
            break
        last_path = window.getProperty("fentastic.%s.path" % list_id)
        cpath_path = xbmc.getInfoLabel("ListItem.FolderPath")
        if last_path == cpath_path or xbmc.getCondVisibility(
            "System.HasActiveModalDialog"
        ):
            continue
        switch_widget = True
        window_gone = False
        countdown = delay
        while not monitor.abortRequested() and countdown >= 0 and switch_widget:
            monitor.waitForAbort(0.25)
            countdown -= 0.25
            grabbed = _grab(stack_id, window_id)
            if grabbed is None:
                window_gone = True
                break
            _, window, stack_control, stack_label_control = grabbed
            if list_id != str(window.getFocusId()):
                switch_widget = False
            if last_path == cpath_path:
                switch_widget = False
            if xbmc.getInfoLabel("ListItem.FolderPath") != cpath_path:
                switch_widget = False
            if xbmc.getCondVisibility("System.HasActiveModalDialog"):
                switch_widget = False
            widget_label = xbmc.getInfoLabel("ListItem.Label")
            if display_delay:
                stack_label_control.setLabel(
                    "Loading [COLOR accent_color][B]{}[/B][/COLOR] in [B]%0.2f[/B] seconds".format(
                        widget_label
                    )
                    % (countdown)
                )
        if window_gone:
            break
        if switch_widget:
            position = int(xbmc.getInfoLabel("Container(%s).Position" % list_id))
            cpath_label = xbmc.getInfoLabel("ListItem.Label")
            stack_label_control.setLabel(cpath_label)
            window.setProperty("fentastic.%s.label" % list_id, cpath_label)
            window.setProperty("fentastic.%s.path" % list_id, cpath_path)
            monitor.waitForAbort(0.2)
            update_wait_time = 0
            while (
                xbmc.getCondVisibility("Container(%s).IsUpdating" % stack_id)
                and not update_wait_time > 3
            ):
                monitor.waitForAbort(0.10)
                update_wait_time += 0.10
            monitor.waitForAbort(0.50)
            grabbed = _grab(stack_id, window_id)
            if grabbed is None:
                break
            try:
                grabbed[2].selectItem(0)
            except:
                pass
        else:
            stack_label_control.setLabel(
                window.getProperty("fentastic.%s.label" % list_id)
            )
            monitor.waitForAbort(0.25)
    try:
        del monitor
    except:
        pass
