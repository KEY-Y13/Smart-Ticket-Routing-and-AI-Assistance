"""Retryable publication-triggered index builds, fenced by a durable lease token."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select

from backend.app.core.errors import AppError
from backend.app.db.models import JobTask, User, utcnow
from backend.app.services.knowledge import build_index


def process_one(database, settings):
    now = utcnow()
    with database.sessions() as session:
        job = session.scalar(
            select(JobTask)
            .where(
                JobTask.type == "BUILD_KNOWLEDGE_INDEX",
                or_(
                    JobTask.status == "QUEUED",
                    (JobTask.status == "RUNNING") & (JobTask.lease_until < now),
                ),
                JobTask.available_at <= now,
            )
            .order_by(JobTask.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return False
        token = uuid4().hex
        job.status, job.lease_token = "RUNNING", token
        job.lease_until = now + timedelta(minutes=5)
        job.attempts += 1
        job_id, actor_id = job.id, job.payload["actor_id"]
        session.commit()
    error = None
    try:
        with database.sessions() as session:
            # Publication authorizes a later background build. Revoked authorizations fail closed.
            user = session.get(User, actor_id)
            if user is None or not user.enabled or user.role != "ADMIN":
                raise AppError(403, "PUBLICATION_AUTHORIZATION_REVOKED", "发布人的管理权限已撤销")
            build_index(session, user, settings, "index-job-" + str(job_id))
    except AppError as exc:
        error = exc.code
    except Exception as exc:
        error = type(exc).__name__
    with database.sessions() as session:
        job = session.scalar(select(JobTask).where(JobTask.id == job_id).with_for_update())
        if job.lease_token != token or job.status != "RUNNING":
            return True
        job.status = "DONE" if error is None else "QUEUED" if job.attempts < 3 else "FAILED"
        job.available_at = utcnow() + timedelta(seconds=30 * job.attempts)
        job.last_error, job.lease_until = error, None
        session.commit()
    return True
