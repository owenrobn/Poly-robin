import json
import os
import logging

logger = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"

DEFAULT_SETTINGS = {
    "entry": 70,
    "flip": 60,
    "tp": 95,
    "stopLoss": 45,
    "buffer": 2,
    "shares": 10,
    "maxTrades": 3,
    "cooldown": 30,
    "autoRedeem": True,
    "master": False
}

class SettingsManager:
    def __init__(self):
        self.settings = {
            "active_profile": "personal",
            "profiles": {
                "personal": DEFAULT_SETTINGS.copy(),
                "general": DEFAULT_SETTINGS.copy()
            }
        }
        self.load()

    def load(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict) and "profiles" in loaded:
                        active = loaded.get("active_profile") or loaded.get("activeProfile") or "personal"
                        profiles = loaded.get("profiles") or {}
                        if isinstance(profiles, dict):
                            for name, p in profiles.items():
                                if not isinstance(p, dict):
                                    continue
                                if name not in self.settings["profiles"]:
                                    self.settings["profiles"][name] = DEFAULT_SETTINGS.copy()
                                self.settings["profiles"][name].update(p)
                        self.settings["active_profile"] = str(active)
                    elif isinstance(loaded, dict):
                        self.settings["profiles"]["personal"].update(loaded)
                logger.info("Settings loaded from settings.json")
            except Exception as e:
                logger.error(f"Failed to load settings: {e}")

    def save(self, new_settings: dict):
        profile = self.settings.get("active_profile", "personal")
        if profile not in self.settings["profiles"]:
            self.settings["profiles"][profile] = DEFAULT_SETTINGS.copy()
        self.settings["profiles"][profile].update(new_settings)
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self.settings, f, indent=4)
            logger.info("Settings saved to settings.json")
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")

    def get(self, key, default=None):
        profile = self.settings.get("active_profile", "personal")
        return self.settings.get("profiles", {}).get(profile, {}).get(key, default)

    def get_all(self):
        profile = self.settings.get("active_profile", "personal")
        return self.settings.get("profiles", {}).get(profile, DEFAULT_SETTINGS.copy())

    def get_active_profile(self):
        return self.settings.get("active_profile", "personal")

    def set_active_profile(self, profile: str):
        name = str(profile or "").strip().lower()
        if not name:
            name = "personal"
        if name not in self.settings["profiles"]:
            self.settings["profiles"][name] = DEFAULT_SETTINGS.copy()
        self.settings["active_profile"] = name
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")

# Global instance
settings_manager = SettingsManager()
