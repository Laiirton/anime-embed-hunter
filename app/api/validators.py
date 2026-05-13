"""
Pydantic validators for API request parameters.

Reduces 500 errors by validating input before processing.
Uses Pydantic V2 syntax.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional
from enum import Enum


def _default_home_url():
    from app.api.utils import _build_url
    return _build_url("/home")


class AudioType(str, Enum):
    """Supported audio types."""
    DUBBED = "Dublado"
    LEGENDED = "Legendado"


class PaginatedRequest(BaseModel):
    """Base model for paginated requests."""
    page: int = Field(default=1, ge=1, le=100000)
    limit: int = Field(default=30, ge=1, le=100)
    
    @field_validator('page', mode='before')
    def parse_page(cls, v):
        if v is None or v == '':
            return 1
        try:
            return int(v)
        except (TypeError, ValueError):
            return 1
    
    @field_validator('limit', mode='before')
    def parse_limit(cls, v, info):
        if v is None or v == '':
            return 30
        try:
            limit = int(v)
            return min(max(1, limit), 100)
        except (TypeError, ValueError):
            return 30


class CatalogRequest(PaginatedRequest):
    """Request model for catalog endpoints."""
    search: Optional[str] = Field(default=None, max_length=200)
    filter_letter: Optional[str] = Field(default=None, min_length=1, max_length=1)
    filter_audio: Optional[str] = Field(default=None)
    order: Optional[str] = Field(
        default="name",
        pattern="^(name|az|name_asc|za|name_desc|recent|updated|newest)$"
    )
    
    @field_validator('filter_audio', mode='before')
    def normalize_audio_filter(cls, v):
        if not v:
            return None
        v = v.strip().lower()
        if v in {"dublado", "dub", "pt-br"}:
            return "dubbed"
        if v in {"legendado", "sub"}:
            return "legendado"
        return None


class SearchRequest(BaseModel):
    """Request model for search endpoints."""
    q: str = Field(..., min_length=1, max_length=200)
    page: int = Field(default=1, ge=1, le=100000)
    limit: int = Field(default=30, ge=1, le=100)
    
    @field_validator('q', mode='before')
    def validate_query(cls, v):
        if not v or not str(v).strip():
            raise ValueError('Query parameter "q" is required')
        return str(v).strip()


class EmbedRequestModel(BaseModel):
    """Request model for embed endpoint."""
    url: str = Field(...)
    force: bool = Field(default=False)
    
    @field_validator('url', mode='before')
    def validate_url(cls, v):
        if not v or not str(v).strip():
            raise ValueError('Parameter "url" is required')
        url = str(v).strip()
        if not url.startswith(('http://', 'https://')):
            raise ValueError('Invalid URL - must start with http:// or https://')
        return url
    
    @field_validator('force', mode='before')
    def parse_force(cls, v):
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        return False


class EpisodePlayersRequest(BaseModel):
    """Request model for episode players endpoint."""
    episode_id: str = Field(...)
    prefix: str = Field(default="a", pattern="^[a-z0-9-]+$")
    
    @field_validator('episode_id', mode='before')
    def validate_episode_id(cls, v):
        if not v or not str(v).strip():
            raise ValueError('Episode id must be provided')
        v = str(v).strip()
        if not v.isdigit():
            raise ValueError('Episode id must be numeric')
        return v


class HomeFeaturedRequest(BaseModel):
    """Request model for home featured endpoint."""
    url: Optional[str] = Field(default=None)
    force: bool = Field(default=False)
    
    @field_validator('url', mode='before')
    def validate_url(cls, v):
        if not v:
            return _default_home_url()
        url = str(v).strip()
        if not url.startswith(('http://', 'https://')):
            raise ValueError('Invalid URL - must start with http:// or https://')
        return url
    
    @field_validator('force', mode='before')
    def parse_force(cls, v):
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        return False


class FavoriteRequest(BaseModel):
    """Request model for favorites endpoints."""
    url: str = Field(..., min_length=1, max_length=500)
    name: Optional[str] = Field(default=None, max_length=255)
    image_url: Optional[str] = Field(default=None, max_length=500)
    user_id: Optional[str] = Field(default=None, max_length=120)


class HistoryRequest(BaseModel):
    """Request model for history endpoints."""
    url: str = Field(..., min_length=1, max_length=500)
    title: Optional[str] = Field(default=None, max_length=255)
    image_url: Optional[str] = Field(default=None, max_length=500)
    user_id: Optional[str] = Field(default=None, max_length=120)
