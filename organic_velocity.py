from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# v4.15.7 Verified Organic Velocity
# A large counter already present when DT first sees an ad has unknown provenance.
# Never let that inherited total vote in demand scoring.  Instead DT establishes a
# baseline and requires two later clean exact measurements before the delta is trusted.
ORGANIC_HIGH_BASELINE_VIEWS = 400
ORGANIC_HIGH_REQUIRED_CHECKPOINTS = 2
ORGANIC_HIGH_CHECKPOINT_MINUTES = 30
ORGANIC_HIGH_BATCH_SIZE = max(5, min(200, int(os.getenv("ORGANIC_HIGH_BATCH_SIZE", "40"))))
ORGANIC_HIGH_POLL_SECONDS = max(30, min(600, int(os.getenv("ORGANIC_HIGH_POLL_SECONDS", "60"))))

HIGH_PENDING_STATUSES = {"high_baseline", "high_check_1"}


@dataclass(frozen=True)
class OrganicMetric:
    views: int | None
    kind: str
    age_minutes: float | None = None


def _status(row: Any) -> str:
    return str(getattr(row, "organic_history_status", "unknown") or "unknown")


def is_high_baseline(row: Any) -> bool:
    baseline = getattr(row, "organic_baseline_views", None)
    return baseline is not None and int(baseline) >= ORGANIC_HIGH_BASELINE_VIEWS


def high_baseline_ready(row: Any) -> bool:
    return (
        is_high_baseline(row)
        and _status(row) == "observed"
        and int(getattr(row, "organic_verified_checkpoints", 0) or 0) >= ORGANIC_HIGH_REQUIRED_CHECKPOINTS
    )


def high_baseline_pending(row: Any) -> bool:
    return is_high_baseline(row) and not high_baseline_ready(row)


def next_high_checkpoint_due_at(row: Any) -> datetime | None:
    if not high_baseline_pending(row):
        return None
    anchor = getattr(row, "organic_last_checkpoint_at", None) or getattr(row, "organic_baseline_at", None)
    if anchor is None:
        return None
    return anchor + timedelta(minutes=ORGANIC_HIGH_CHECKPOINT_MINUTES)


def apply_organic_measurement(row: Any, views: int, measured_at: datetime) -> str:
    """Update the provenance state for one clean exact view measurement.

    Return a compact transition label for telemetry.  This routine never classifies
    an ad as promoted from view volume; it only decides whether the counter may vote
    in organic demand scoring.
    """
    if bool(getattr(row, "is_promoted", False)) or bool(getattr(row, "is_price_reduced", False)):
        return "dirty"

    raw = max(0, int(views))
    status = _status(row)
    baseline_at = getattr(row, "organic_baseline_at", None)
    baseline_views = getattr(row, "organic_baseline_views", None)

    if baseline_at is None or baseline_views is None:
        row.organic_baseline_views = raw
        row.organic_baseline_at = measured_at
        row.organic_verified_checkpoints = 0
        row.organic_last_checkpoint_at = measured_at
        row.organic_last_checkpoint_views = raw
        if raw >= ORGANIC_HIGH_BASELINE_VIEWS:
            # Even a same-day listing can have inherited paid/repost traffic.  At
            # 400+ initial views, the total is evidence-unknown by policy.
            row.organic_history_status = "high_baseline"
            return "high_baseline_started"
        row.organic_history_status = "trusted" if status in {"trusted", "trusted_new"} else "baseline"
        return "trusted_total" if row.organic_history_status == "trusted" else "baseline_started"

    # v4.15.7 threshold applies to the FIRST DT-observed counter.  Once that first
    # counter is 400+, crossing below/above 400 later never changes the policy.
    if int(baseline_views) >= ORGANIC_HIGH_BASELINE_VIEWS:
        # Repair rows created by v4.15.6 where same-day 400+ totals were marked trusted.
        if status not in HIGH_PENDING_STATUSES and status != "observed":
            row.organic_history_status = "high_baseline"
            row.organic_verified_checkpoints = 0
            row.organic_last_checkpoint_at = baseline_at
            row.organic_last_checkpoint_views = int(baseline_views)
            status = "high_baseline"

        if status == "observed" and int(getattr(row, "organic_verified_checkpoints", 0) or 0) >= ORGANIC_HIGH_REQUIRED_CHECKPOINTS:
            # Already certified. Keep latest exact point for diagnostics but do not
            # inflate the certification counter forever.
            last_at = getattr(row, "organic_last_checkpoint_at", None)
            if last_at is None or measured_at > last_at:
                row.organic_last_checkpoint_at = measured_at
                row.organic_last_checkpoint_views = raw
            return "high_verified_refresh"

        last_at = getattr(row, "organic_last_checkpoint_at", None) or baseline_at
        last_views = getattr(row, "organic_last_checkpoint_views", None)
        if last_views is None:
            last_views = int(baseline_views)
        # Counter rollback is not usable as organic growth evidence.
        if raw < int(last_views):
            return "counter_rollback"
        # Two calls from one batch/retry must not masquerade as two checkpoints.
        if measured_at <= last_at or measured_at - last_at < timedelta(minutes=ORGANIC_HIGH_CHECKPOINT_MINUTES):
            return "high_waiting_interval"

        checkpoints = min(
            ORGANIC_HIGH_REQUIRED_CHECKPOINTS,
            int(getattr(row, "organic_verified_checkpoints", 0) or 0) + 1,
        )
        row.organic_verified_checkpoints = checkpoints
        row.organic_last_checkpoint_at = measured_at
        row.organic_last_checkpoint_views = raw
        if checkpoints >= ORGANIC_HIGH_REQUIRED_CHECKPOINTS:
            row.organic_history_status = "observed"
            return "high_verified"
        row.organic_history_status = "high_check_1"
        return "high_checkpoint_1"

    # Existing v4.15.6 behaviour for lower-volume ambiguous history: one later clean
    # measurement makes only the delta after DT's baseline usable.
    if status in {"unknown", "baseline"} and measured_at > baseline_at and raw >= int(baseline_views):
        row.organic_history_status = "observed"
        row.organic_verified_checkpoints = max(1, int(getattr(row, "organic_verified_checkpoints", 0) or 0))
        row.organic_last_checkpoint_at = measured_at
        row.organic_last_checkpoint_views = raw
        return "observed_delta"

    if status in {"trusted", "trusted_new"}:
        row.organic_history_status = "trusted"
        return "trusted_refresh"
    return "unchanged"


def demand_safe_metric(row: Any, raw_views: int | None, measured_at: datetime | None = None) -> OrganicMetric:
    """Return the only view quantity allowed to vote in Radar/DT Demand Score."""
    if raw_views is None:
        return OrganicMetric(None, "missing_views", None)
    raw = max(0, int(raw_views))
    status = _status(row)
    baseline = getattr(row, "organic_baseline_views", None)
    baseline_at = getattr(row, "organic_baseline_at", None)

    if baseline is not None and int(baseline) >= ORGANIC_HIGH_BASELINE_VIEWS:
        checkpoints = int(getattr(row, "organic_verified_checkpoints", 0) or 0)
        if status != "observed" or checkpoints < ORGANIC_HIGH_REQUIRED_CHECKPOINTS:
            return OrganicMetric(None, "high_baseline_pending", None)
        if measured_at is None or baseline_at is None or measured_at <= baseline_at:
            return OrganicMetric(None, "high_baseline_clock_missing", None)
        delta = max(0, raw - int(baseline))
        age_minutes = max(0.0, (measured_at - baseline_at).total_seconds() / 60.0)
        return OrganicMetric(delta, "observed_delta", age_minutes)

    if status in {"unknown", "baseline", "high_baseline", "high_check_1"}:
        return OrganicMetric(None, "history_baseline_pending", None)
    if status == "observed" and baseline is not None:
        if measured_at is None or baseline_at is None or measured_at <= baseline_at:
            return OrganicMetric(None, "observed_clock_missing", None)
        delta = max(0, raw - int(baseline))
        age_minutes = max(0.0, (measured_at - baseline_at).total_seconds() / 60.0)
        return OrganicMetric(delta, "observed_delta", age_minutes)
    return OrganicMetric(raw, "trusted_total", None)
