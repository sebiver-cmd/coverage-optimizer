"""Plan definitions for SB-Optima (Task 7.2).

Defines first-class "plan" support so a tenant's daily limits can be derived
from a plan (Free / Pro / Enterprise) and later synced to Stripe.

Provides:
- :class:`Plan` — a dataclass describing a plan's default limits.
- ``PLANS`` — a dict mapping plan names to :class:`Plan` instances.
- :func:`get_plan` — look up a plan by name (case-insensitive).
- :func:`list_plans` — return all known plans (safe to expose publicly).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class Plan:
    """Immutable descriptor for a billing plan's default daily limits.

    ``None`` means *unlimited* for that action.
    """

    name: str
    daily_optimize_jobs_limit: Optional[int] = None
    daily_apply_limit: Optional[int] = None
    daily_optimize_sync_limit: Optional[int] = None

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict of the plan."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Built-in plans
# ---------------------------------------------------------------------------

PLANS: dict[str, Plan] = {
    "free": Plan(
        name="free",
        daily_optimize_jobs_limit=25,
        daily_apply_limit=0,
        daily_optimize_sync_limit=10,
    ),
    "pro": Plan(
        name="pro",
        daily_optimize_jobs_limit=200,
        daily_apply_limit=50,
        daily_optimize_sync_limit=200,
    ),
    "enterprise": Plan(
        name="enterprise",
        daily_optimize_jobs_limit=None,  # unlimited
        daily_apply_limit=None,
        daily_optimize_sync_limit=None,
    ),
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_plan(name: str) -> Plan | None:
    """Return the :class:`Plan` for *name* (case-insensitive), or ``None``."""
    return PLANS.get(name.lower()) if name else None


def list_plans() -> list[Plan]:
    """Return all available plans (safe to expose via API)."""
    return list(PLANS.values())
