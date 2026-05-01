import json
import os
import logging

class SiteManager:
    def __init__(self, config_path='configs.json'):
        self.config_path = config_path
        self.configs = {}
        self.reload_configs()

    def reload_configs(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.configs = json.load(f)
                logging.info(f"Configurations loaded from {self.config_path}")
                return True
            else:
                logging.error(f"Config file not found: {self.config_path}")
                return False
        except Exception as e:
            logging.error(f"Error loading configs: {e}")
            return False

    def get_config_for_url(self, url):
        for site_key, cfg in self.configs.items():
            domain = cfg.get('domain')
            if domain and domain in url:
                return site_key, cfg
        return None, None

# Singleton instance
site_manager = SiteManager()
