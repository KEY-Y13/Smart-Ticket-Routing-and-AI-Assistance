"""Explicit initial configuration. Never called during application startup."""

from sqlalchemy import select

from backend.app.db.models import DispatchMutex, SLAPolicy

# One place for initial fictional policies; runtime always reads database snapshots.
INITIAL_SLA = (
    (0, 900, 14400, 300),
    (1, 3600, 43200, 600),
    (2, 14400, 172800, 1800),
    (3, 43200, 432000, 3600),
)


def initialize_policies(session):
    if session.get(DispatchMutex, 1) is None:
        session.add(DispatchMutex(id=1))
    for priority, response, resolution, warning in INITIAL_SLA:
        exists = session.scalar(
            select(SLAPolicy.id).where(
                SLAPolicy.policy_code == "initial-natural-hours",
                SLAPolicy.version == 1,
                SLAPolicy.priority == priority,
            )
        )
        if exists is None:
            session.add(
                SLAPolicy(
                    policy_code="initial-natural-hours",
                    version=1,
                    priority=priority,
                    response_seconds=response,
                    resolution_seconds=resolution,
                    warning_seconds=warning,
                    active=True,
                )
            )
