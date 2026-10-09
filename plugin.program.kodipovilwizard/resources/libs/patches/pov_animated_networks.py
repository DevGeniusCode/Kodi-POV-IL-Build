# Path: special://home/addons/plugin.program.kodipovilwizard/resources/libs/patches/pov_animated_networks.py
# Purpose: Dynamically swaps static network shortcut icons to animated ones based on the Fentastic skin's animation setting.

import xbmc
import xbmcvfs

# Mapping of static icons to animated icons.
# Supports full paths, relative paths, or exact URLs.
ICON_MAP = {
    'Shows_Amazon.png': 'Animated_Amazon.gif',
    'Shows_Netflix.png': 'Animated_Netflix.gif',
    'Shows_Disney_Plus.png': 'Animated_Disney_Plus.gif',
    'Shows_AppleTV.png': 'Animated_AppleTV.gif',
    'Shows_HBO_Max.png': 'Animated_HBO_Max.gif',
    'Shows_Hulu.png': 'Animated_Hulu.gif',
    'Shows_CW.png': 'Animated_CW.gif'
    }

def run(navigator_cache_instance):
    # Store original method
    original_get_contents = navigator_cache_instance.get_shortcut_folder_contents

    def patched_get_shortcut_folder_contents(list_name):
        # Retrieve original list
        items = original_get_contents(list_name)

        # Check Fentastic skin setting (empty means animations are ENABLED)
        animations_disabled = xbmc.getInfoLabel('Skin.HasSetting(no_slide_animations)')

        # Apply animated icons if animations are active
        if not animations_disabled and isinstance(items, list):
            for item in items:
                if 'iconImage' in item and isinstance(item['iconImage'], str):
                    current_icon = item['iconImage']

                    # Iterate through mapping and replace if a match is found
                    for static_path, animated_path in ICON_MAP.items():
                        if static_path in current_icon:
                            potential_animated_icon = current_icon.replace(static_path, animated_path)

                            if potential_animated_icon.startswith('http'):
                                item['iconImage'] = potential_animated_icon
                            elif xbmcvfs.exists(potential_animated_icon):
                                item['iconImage'] = potential_animated_icon

                            break

        return items

    # Apply monkey patch
    navigator_cache_instance.get_shortcut_folder_contents = patched_get_shortcut_folder_contents