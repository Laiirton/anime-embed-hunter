from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.models.embed import EmbedRequest, db


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def delete_expired_embed_cache(batch_size=1000, now=None):
    cutoff = now or _utcnow()
    safe_batch_size = max(100, int(batch_size))
    total_deleted = 0

    while True:
        expired_ids = db.session.execute(
            select(EmbedRequest.id)
            .where(EmbedRequest.expires_at <= cutoff)
            .order_by(EmbedRequest.id)
            .limit(safe_batch_size)
        ).scalars().all()

        if not expired_ids:
            break

        db.session.execute(delete(EmbedRequest).where(EmbedRequest.id.in_(expired_ids)))
        db.session.commit()
        total_deleted += len(expired_ids)

        if len(expired_ids) < safe_batch_size:
            break

    return total_deleted
