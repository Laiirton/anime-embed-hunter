from pydantic import BaseModel
from typing import Dict, List, Optional

class Selectors(BaseModel):
    episodes_section: Optional[str] = None
    title: Optional[str] = None
    iframe_selectors: Optional[List[str]] = None
    pagination: Optional[str] = None
    item_selector: Optional[str] = None
    pagination_info: Optional[str] = None
    url_pattern: Optional[str] = None
    home_sections: Optional[Dict[str, str]] = None

class SiteConfig(BaseModel):
    domain: str
    url_patterns: Dict[str, str]
    selectors: Dict[str, Selectors]
    bypass_javascript: bool

class AppConfig(BaseModel):
    sites: Dict[str, SiteConfig]
