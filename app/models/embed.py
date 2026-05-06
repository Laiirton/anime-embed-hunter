from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Episode(db.Model):
    __tablename__ = 'episodes'
    __table_args__ = (
        db.Index('idx_episodes_anime_id', 'anime_id'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    anime_id = db.Column(db.Integer, db.ForeignKey('animes.id'), nullable=True)
    title = db.Column(db.String(255))
    url = db.Column(db.String(500), unique=True, nullable=False, index=True)
    embed_url = db.Column(db.Text)
    last_updated = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    
    anime = db.relationship('Anime', backref=db.backref('episodes', lazy='selectin'))

    def to_dict(self):
        return {
            'title': self.title,
            'url': self.url,
            'embed_url': self.embed_url,
            'last_updated': self.last_updated.isoformat() if self.last_updated else None
        }

class Anime(db.Model):
    __tablename__ = 'animes'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(500), unique=True, nullable=False, index=True)
    item_type = db.Column(db.String(50), default='series') # series, movie
    cover_url = db.Column(db.String(500), nullable=True)
    last_scanned = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'url': self.url,
            'item_type': self.item_type,
            'cover_url': self.cover_url,
            'last_scanned': self.last_scanned.isoformat() if self.last_scanned else None,
            'episodes_count': len(self.episodes) if hasattr(self, 'episodes') else 0
        }

class EmbedRequest(db.Model):
    __tablename__ = 'embed_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), unique=True, nullable=False, index=True)
    response_data = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)

    def to_dict(self):
        import json
        return json.loads(self.response_data)


class Favorite(db.Model):
    __tablename__ = "favorites"
    __table_args__ = (
        db.UniqueConstraint("profile_key", "anime_url", name="uq_favorites_profile_anime_url"),
    )

    id = db.Column(db.Integer, primary_key=True)
    profile_key = db.Column(db.String(120), nullable=False, index=True)
    anime_id = db.Column(db.Integer, db.ForeignKey("animes.id"), nullable=True, index=True)
    anime_name = db.Column(db.String(255), nullable=False)
    anime_url = db.Column(db.String(500), nullable=False)
    image_url = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False, index=True)

    anime = db.relationship("Anime", backref=db.backref("favorites", lazy="dynamic"))

    def to_dict(self):
        return {
            "id": self.id,
            "profile_key": self.profile_key,
            "anime_id": self.anime_id,
            "anime_name": self.anime_name,
            "anime_url": self.anime_url,
            "image_url": self.image_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class HistoryEntry(db.Model):
    __tablename__ = "history_entries"
    __table_args__ = (
        db.UniqueConstraint("profile_key", "content_url", name="uq_history_profile_content_url"),
    )

    id = db.Column(db.Integer, primary_key=True)
    profile_key = db.Column(db.String(120), nullable=False, index=True)
    anime_id = db.Column(db.Integer, db.ForeignKey("animes.id"), nullable=True, index=True)
    episode_id = db.Column(db.Integer, db.ForeignKey("episodes.id"), nullable=True, index=True)
    title = db.Column(db.String(255), nullable=False)
    content_url = db.Column(db.String(500), nullable=False)
    image_url = db.Column(db.Text, nullable=True)
    watch_count = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_seen = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False, index=True)

    anime = db.relationship("Anime", backref=db.backref("history_entries", lazy="dynamic"))
    episode = db.relationship("Episode", backref=db.backref("history_entries", lazy="dynamic"))

    def to_dict(self):
        return {
            "id": self.id,
            "profile_key": self.profile_key,
            "anime_id": self.anime_id,
            "episode_id": self.episode_id,
            "title": self.title,
            "url": self.content_url,
            "image_url": self.image_url,
            "watch_count": self.watch_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }
