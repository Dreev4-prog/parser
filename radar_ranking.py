from __future__ import annotations

from dataclasses import dataclass

RADAR_48H_MAX_AGE_MINUTES = 48.0 * 60.0

# Absolute demand floor by observed age.  DT Score remains relative, but a listing
# cannot be called Hot while the absolute amount of demand is still trivial.
_DEMAND_GATES: tuple[tuple[float, int], ...] = (
    (3.0 * 60.0, 30),
    (6.0 * 60.0, 40),
    (12.0 * 60.0, 60),
    (24.0 * 60.0, 80),
    (48.0 * 60.0, 100),
)


@dataclass(frozen=True)
class RadarRankEvidence:
    status: str
    radar_rank: float
    demand_gate: int
    demand_ratio: float
    maturity_score: float
    admitted: bool


def demand_gate_for_age(age_minutes: float | None) -> int:
    if age_minutes is None:
        return 10**9
    age = max(0.0, float(age_minutes))
    for upper, gate in _DEMAND_GATES:
        if age <= upper:
            return int(gate)
    return 10**9


def maturity_score_for_age(age_minutes: float | None) -> float:
    """Evidence maturity used only for ordering, never for DT Demand Score itself."""
    if age_minutes is None:
        return 0.0
    age = max(0.0, float(age_minutes))
    if age <= 3.0 * 60.0:
        return 35.0
    if age <= 6.0 * 60.0:
        return 50.0
    if age <= 12.0 * 60.0:
        return 65.0
    if age <= 24.0 * 60.0:
        return 82.0
    if age <= 48.0 * 60.0:
        return 100.0
    return 0.0


def classify_radar_signal(
    *, dt_score: int | float, confidence: int | float,
    demand_views: int | float | None, age_minutes: float | None,
) -> RadarRankEvidence:
    """Classify one demand-safe signal for the unified 48H Radar.

    DT Demand Score stays 40/20/15/15/10.  This layer only decides whether the
    amount of *verified* demand is sufficient to call that relative score Early,
    Strong or Hot, and produces a separate internal ranking value.
    """
    score = max(0.0, min(100.0, float(dt_score or 0.0)))
    conf = max(0.0, min(100.0, float(confidence or 0.0)))
    demand = max(0.0, float(demand_views or 0.0))
    age = None if age_minutes is None else max(0.0, float(age_minutes))
    maturity = maturity_score_for_age(age)
    gate = demand_gate_for_age(age)
    if gate >= 10**9 or age is None or age < 5.0 or age > RADAR_48H_MAX_AGE_MINUTES:
        return RadarRankEvidence("historical", 0.0, gate, 0.0, maturity, False)

    ratio = demand / max(1.0, float(gate))
    # Ordering is deliberately separate from the public DT Score.  Mature evidence
    # can beat a very young thin signal without rewriting the 40/20/15/15/10 score.
    rank = max(0.0, min(100.0, 0.70 * score + 0.20 * conf + 0.10 * maturity))

    # Full Hot requires both relative strength and absolute demand evidence.
    if score >= 72.0 and conf >= 45.0 and ratio >= 1.0:
        return RadarRankEvidence("hot", rank, gate, ratio, maturity, True)
    # Strong is useful before the full absolute gate, but never appears as Hot.
    if score >= 65.0 and conf >= 35.0 and ratio >= 0.60:
        return RadarRankEvidence("rising", rank, gate, ratio, maturity, True)
    # Early keeps genuinely interesting fresh listings visible in the catalogue,
    # while a 15-view listing can no longer occupy the Hot TOP.
    if score >= 58.0 and ratio >= 0.25:
        return RadarRankEvidence("stable", rank, gate, ratio, maturity, True)
    return RadarRankEvidence("historical", 0.0, gate, ratio, maturity, False)
