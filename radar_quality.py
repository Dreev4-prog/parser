"""Pure, deterministic Radar evidence policies. No network or database access."""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

ACTIVE_OBSERVATION_STATUSES = ("baseline", "exploring", "candidate", "observed", "confirmed", "rollback_pending", "identity_reset")
SCORABLE_OBSERVATION_STATUSES = ("candidate", "observed", "confirmed")
EXPLORATION_INTERVAL_MINUTES = 120
EXPLORATION_BATCH_LIMIT = 30
ROLLBACK_RETRY_MINUTES = 10
ROLLBACK_REQUIRED_RECOVERIES = 2
ABSOLUTE_GATES = {"candidate": 8.0, "early": 15.0, "strong": 30.0, "hot": 60.0}
QUANTILES = {"candidate": .90, "early": .95, "strong": .98, "hot": .99}

@dataclass(frozen=True)
class CohortPosition:
    percentile: float
    count: int
    tie_fraction: float
    above_median: bool
    rankable: bool

def cohort_position(value: float, peers: list[float]) -> CohortPosition:
    """One population for thresholds and ranks, with an explicit ties policy.

    A tied top group is eligible only if it fits the requested percentile tail.
    In a small cohort one item is the minimum sensible tail size. A flat cohort
    has no statistical winner, whatever the absolute velocity may be.
    """
    vals = sorted(float(x) for x in peers if math.isfinite(float(x)) and float(x) >= 0)
    if not vals or not math.isfinite(float(value)):
        return CohortPosition(0.0, 0, 1.0, False, False)
    below = sum(x < value for x in vals)
    equal = sum(x == value for x in vals)
    median = vals[(len(vals)-1)//2] if len(vals)%2 else (vals[len(vals)//2-1]+vals[len(vals)//2])/2
    return CohortPosition(min(1.0, (below+equal)/len(vals)),len(vals),equal/len(vals),value>median,value>vals[0])

def cohort_thresholds(peers: list[float]) -> dict[str, float]:
    vals=sorted(float(x) for x in peers if math.isfinite(float(x)) and float(x)>=0)
    def quantile(q):
        if not vals: return 0.0
        pos=(len(vals)-1)*q;lo=int(pos);hi=min(len(vals)-1,lo+1)
        return vals[lo]+(vals[hi]-vals[lo])*(pos-lo)
    result={k:max(floor,quantile(QUANTILES[k])) for k,floor in ABSOLUTE_GATES.items()}
    result['peer_count']=len(vals)
    return result

def qualifies_velocity(value: float, peers: list[float], tier: str, thresholds=None) -> bool:
    if tier not in QUANTILES: raise ValueError(tier)
    thresholds=thresholds or cohort_thresholds(peers)
    position=cohort_position(value,peers)
    if value < thresholds[tier] or value < 3.0: return False
    # Sparse cohorts use conservative absolute floors; no invented percentile.
    if position.count < 20: return True
    max_tie=max(1,math.floor(position.count*(1.0-QUANTILES[tier])+1e-9))/position.count
    return (position.rankable and position.above_median and
            position.percentile>=QUANTILES[tier] and position.tie_fraction<=max_tie+1e-9)

def next_exploration_at(measured_at:datetime, expires_at:datetime|None, count:int)->datetime|None:
    """A weak first hour does not terminate the six-hour evidence window."""
    if count>=4: return None
    due=measured_at+timedelta(minutes=EXPLORATION_INTERVAL_MINUTES)
    return due if expires_at is None or due<=expires_at else None

def rollback_transition(anchor:int, current:int, previous_low:int|None, recoveries:int)->tuple[str,int,int|None]:
    """Never lower a trusted anchor. Two nondecreasing recovery samples required."""
    if current<anchor:
        return 'pending',0,current
    if previous_low is None: return 'normal',0,None
    if current<previous_low: return 'pending',0,current
    recoveries+=1
    if recoveries>=ROLLBACK_REQUIRED_RECOVERIES: return 'recovered',recoveries,current
    return 'pending',recoveries,current
