from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Episode(db.Model):
    __tablename__ = 'episodes'
    
    id = db.Column(db.Integer, primary_key=True)
    anime_id = db.Column(db.Integer, db.ForeignKey('animes.id'), nullable=True)
    title = db.Column(db.String(255))
    url = db.Column(db.String(500), unique=True, nullable=False, index=True)
    embed_url = db.Column(db.Text)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    anime = db.relationship('Anime', backref=db.backref('episodes', lazy=True))

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
    last_scanned = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'url': self.url,
            'item_type': self.item_type,
            'last_scanned': self.last_scanned.isoformat() if self.last_scanned else None,
            'episodes_count': len(self.episodes) if hasattr(self, 'episodes') else 0
        }

class EmbedRequest(db.Model):
    __tablename__ = 'embed_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), unique=True, nullable=False, index=True)
    response_data = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        import json
        return json.loads(self.response_data)
