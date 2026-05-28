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
    "autoRedeem": True
}

class SettingsManager:
    def __init__(self):
        self.settings = DEFAULT_SETTINGS.copy()
        self.load()

    def load(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f:
                    loaded = json.load(f)
                    self.settings.update(loaded)
                logger.info("Settings loaded from settings.json")
            except Exception as e:
                logger.error(f"Failed to load settings: {e}")

    def save(self, new_settings: dict):
        self.settings.update(new_settings)
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self.settings, f, indent=4)
            logger.info("Settings saved to settings.json")
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")

    def get(self, key, default=None):
        return self.settings.get(key, default)

    def get_all(self):
        return self.settings

# Global instance
settings_manager = SettingsManager()
