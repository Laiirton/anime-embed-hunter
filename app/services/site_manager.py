import json
import os
import logging
from app.core.config_model import AppConfig

from urllib.parse import urlparse

class SiteManager:
    def __init__(self, config_path=None):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        self.config_path = config_path or os.path.join(base_dir, "configs.json")
        self.configs = {}
        self.reload_configs()

    def reload_configs(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    raw_configs = json.load(f)
                    # Validação Pydantic
                    validated_configs = AppConfig(sites=raw_configs)
                    self.configs = validated_configs.sites
                logging.info(f"Configurations loaded and validated from {self.config_path}")
                return True
            else:
                logging.error(f"Config file not found: {self.config_path}")
                return False
        except Exception as e:
            logging.error(f"Error loading configs: {e}")
            return False

    def get_config_for_url(self, url):
        try:
            hostname = (urlparse(url).hostname or "").lower()
        except ValueError:
            return None, None

        for site_key, cfg in self.configs.items():
            # Agora cfg é uma instância de SiteConfig (Pydantic), não um dict
            domain = (getattr(cfg, 'domain', '') or "").lower()
            if domain and (hostname == domain or hostname.endswith(f".{domain}")):
                return site_key, cfg
        return None, None

# Singleton instance
site_manager = SiteManager()
