from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from product_identity import canonical_text
from zoneinfo import ZoneInfo

MODEL_VERSION = "ew-opportunity-v2"
BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def percentile_value(values: Iterable[float], q: float) -> float:
    vals = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    q = clamp(q)
    pos = (len(vals) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def percentile_rank(value: float, values: Iterable[float]) -> float:
    vals = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not vals:
        return 0.5
    if len(vals) == 1:
        return 0.5
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    return clamp((below + 0.5 * equal) / len(vals))


def _as_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def listing_age_minutes(posted_text: str | None, captured_at: datetime) -> tuple[float | None, bool]:
    """Infer listing age at the scan snapshot.

    Returns ``(minutes, exact_clock)``. Early Winner deliberately requires a
    timestamp with a clock for fresh candidates. Explicit date-only cards are
    still useful to the normal parser, but are too imprecise for velocity AI.
    This helper never changes the parser's Moscow-date semantics.
    """
    if not posted_text:
        return None, False
    raw = " ".join(str(posted_text).split())
    captured_utc = _as_utc_aware(captured_at)
    captured_berlin = captured_utc.astimezone(BERLIN)

    iso = re.search(
        r"\b(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2})(?::(\d{2}))?(Z|[+-]\d{2}:?\d{2})?)?",
        raw,
    )
    if iso and iso.group(4):
        try:
            text = iso.group(0).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BERLIN)
            delta = captured_utc - dt.astimezone(UTC)
            return max(0.0, delta.total_seconds() / 60.0), True
        except (ValueError, TypeError):
            pass

    explicit = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})(?:,?\s*(\d{1,2}):(\d{2}))?\b", raw)
    if explicit:
        if not explicit.group(4):
            return None, False
        try:
            dt = datetime(
                int(explicit.group(3)), int(explicit.group(2)), int(explicit.group(1)),
                int(explicit.group(4)), int(explicit.group(5)), tzinfo=BERLIN,
            )
            delta = captured_utc - dt.astimezone(UTC)
            return max(0.0, delta.total_seconds() / 60.0), True
        except ValueError:
            return None, False

    lower = raw.lower()
    if "heute" not in lower and "gestern" not in lower:
        return None, False
    tm = re.search(r"\b(\d{1,2}):(\d{2})\b", raw)
    if not tm:
        return None, False
    base = captured_berlin.date() - (timedelta(days=1) if "gestern" in lower else timedelta(0))
    try:
        dt = datetime(base.year, base.month, base.day, int(tm.group(1)), int(tm.group(2)), tzinfo=BERLIN)
    except ValueError:
        return None, False
    delta = captured_utc - dt.astimezone(UTC)
    return max(0.0, delta.total_seconds() / 60.0), True


@dataclass(frozen=True)
class FeatureRow:
    external_id: str
    category_key: str
    identity_key: str | None
    identity_label: str | None
    identity_confidence: int | None
    price_eur: int | None
    views: int
    age_minutes: float
    title: str = ""
    family_key: str | None = None

    @property
    def views_per_hour(self) -> float:
        return float(self.views) / max(0.25, self.age_minutes / 60.0)


@dataclass(frozen=True)
class InitialScore:
    external_id: str
    score: int
    confidence: int
    stage: str
    views_per_hour: float
    velocity_percentile: float
    views_percentile: float
    peer_count: int
    peer_vph_median: float
    peer_vph_p85: float
    peer_vph_p90: float
    market_median_eur: float | None
    market_cohort_size: int
    price_delta_pct: float | None
    predicted_3h_low: int
    predicted_3h_high: int
    predicted_6h_low: int
    predicted_6h_high: int
    reasons: tuple[str, ...]
    cohort_key: str = ""
    supply_fit: float = 0.0
    mass_penalty: float = 0.0
    anomaly_ratio: float = 1.0
    opportunity_type: str = "anomaly"


@dataclass(frozen=True)
class DynamicScore:
    score: int
    stage: str
    observed_views_per_hour: float
    recent_views_per_hour: float
    momentum_ratio: float
    strength: float
    outcome: str
    reasons: tuple[str, ...]


def stage_for_score(score: int) -> str:
    if int(score) >= 90:
        return "early_winner"
    if int(score) >= 80:
        return "rising"
    return "watch"


def _price_factor(price: int | None, median: float | None) -> tuple[float, float | None]:
    if price is None or median is None or median <= 0:
        # Missing market history must be neutral. In v4.5.0 it indirectly punished
        # unknown products, while well-recognised mass electronics got a free edge.
        return 0.5, None
    delta = (float(median) - float(price)) / float(median)
    # Price is a supporting signal only: 20% under market -> 1.0; median -> 0.5.
    return clamp(0.5 + delta * 2.5), delta * 100.0


_FAMILY_STOPWORDS = {
    "verkaufe", "verkaufen", "biete", "angebot", "neu", "neuwertig", "gebraucht",
    "top", "sehr", "gut", "guter", "gute", "gutes", "zustand", "original", "ovp",
    "mit", "ohne", "und", "oder", "fur", "fuer", "von", "der", "die", "das", "ein",
    "eine", "einer", "einem", "inkl", "inklusive", "versand", "abholung", "privat",
    "set", "bundle", "schwarz", "weiss", "white", "black", "blau", "blue", "rot", "red",
}


def opportunity_family_key(title: str, category_key: str = "") -> str:
    """Best-effort deterministic family for products the strict identity parser does not know.

    The signature is deliberately order-tolerant. If a model-like alphanumeric code
    exists (DHP484Z, ZX991, X100V), the family primarily uses brand/name + that code.
    Otherwise it uses a sorted pair of stable title words (e.g. DeLonghi + ECAM +
    Magnifica). Cosmetic/sales words and simple voltage/storage specs are ignored.
    """
    text = canonical_text(title or "")
    if not text:
        return ""
    tokens = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text)
    useful = [t for t in tokens if len(t) >= 2 and t not in _FAMILY_STOPWORDS]
    if not useful:
        return ""

    def spec_like(token: str) -> bool:
        return bool(re.fullmatch(r"\d+(?:gb|tb|v|w|kw|mah|ah|mm|cm|hz|kg)", token))

    mixed = [
        t for t in useful
        if re.search(r"[a-z]", t) and re.search(r"\d", t) and not spec_like(t)
    ]
    alpha = [t for t in useful if t.isalpha() and len(t) >= 3]
    if not alpha and not mixed:
        return ""

    head = alpha[0] if alpha else "product"
    if mixed:
        # Model codes are more stable than words like Akku/Solo/Set around them.
        tail = sorted(dict.fromkeys(mixed[:2]))
    else:
        tail = sorted(dict.fromkeys(alpha[1:3]))
    chosen = [head] + tail
    if len(chosen) < 2:
        return ""
    return f"fam:{category_key or 'unknown'}:" + "|".join(chosen)


def opportunity_cohort_key(row: FeatureRow) -> str:
    if row.identity_key and int(row.identity_confidence or 0) >= 70:
        return f"id:{row.identity_key}"
    if row.family_key:
        return row.family_key
    return opportunity_family_key(row.title, row.category_key)


def _market_tuple(value) -> tuple[float | None, int]:
    """Accept v4.5 tuple stats and the v4.5.1 keyed stats format."""
    if value is None:
        return None, 0
    if isinstance(value, tuple) and len(value) >= 2:
        median, count = value[0], value[1]
        return (float(median) if median is not None else None), int(count or 0)
    if isinstance(value, dict):
        median = value.get("median")
        return (float(median) if median is not None else None), int(value.get("count") or 0)
    return None, 0


def _supply_fit(count: int) -> float:
    """Sweet-spot curve: neither mass-market nor one-off dead inventory wins by rarity alone."""
    n = max(0, int(count or 0))
    if n == 0:
        return 0.75  # unseen/new family: neutral-positive, but must win on demand/anomaly
    if n <= 2:
        return 0.30  # too little evidence to call a niche attractive
    if n <= 5:
        return 0.65
    if n <= 35:
        return 1.00  # ideal discovery zone
    if n <= 75:
        return 0.82
    if n <= 120:
        return 0.58
    if n <= 200:
        return 0.32
    return 0.12


def _mass_penalty(count: int, scan_share: float) -> float:
    """0..35 score penalty for saturated families.

    Historical supply is primary; current-scan share catches a mass family even when
    history is still warming up. The penalty is intentionally smooth around boundaries.
    """
    n = max(0, int(count or 0))
    historical = 0.0
    if n > 60:
        historical = min(35.0, (n - 60) / 190.0 * 35.0)
    share_penalty = 0.0
    if scan_share > 0.12:
        share_penalty = min(12.0, (scan_share - 0.12) / 0.28 * 12.0)
    return min(35.0, historical + share_penalty)


def _ratio_factor(ratio: float) -> float:
    # 0.5x -> 0; 1x -> .33; 2x -> .67; 4x+ -> 1.0.
    ratio = max(0.05, float(ratio))
    return clamp((math.log(ratio, 2.0) + 1.0) / 3.0)


def _absolute_demand_factor(views: int, vph: float) -> float:
    # Stops a tiny category where "3 views vs 1 view" from looking like a huge opportunity.
    vph_part = clamp(math.log1p(max(0.0, vph)) / math.log1p(30.0))
    views_part = clamp(math.log1p(max(0, int(views))) / math.log1p(120.0))
    return 0.72 * vph_part + 0.28 * views_part


def _opportunity_type(market_count: int, mass_penalty: float, anomaly_ratio: float, peer_count: int) -> str:
    if mass_penalty >= 12.0:
        return "saturated"
    if 3 <= market_count <= 60 and anomaly_ratio >= 1.8:
        return "hidden_gem"
    if peer_count >= 3 and market_count <= 120 and anomaly_ratio >= 1.45:
        return "emerging"
    return "anomaly"


def score_initial_rows(
    rows: list[FeatureRow],
    market_stats: dict[str, tuple[float, int]] | None = None,
) -> list[InitialScore]:
    """Opportunity Discovery v2.

    The score answers a different question than TOP views: "is demand unusually strong
    *relative to supply*?" Recognition confidence only affects confidence, never Score.
    """
    market_stats = market_stats or {}
    by_category: dict[str, list[FeatureRow]] = {}
    for row in rows:
        by_category.setdefault(row.category_key or "unknown", []).append(row)

    result: list[InitialScore] = []
    for category_rows in by_category.values():
        category_rates = [r.views_per_hour for r in category_rows]
        view_values = [float(r.views) for r in category_rows]

        cohort_groups: dict[str, list[FeatureRow]] = {}
        cohort_key_by_id: dict[str, str] = {}
        ungrouped_rates: list[float] = []
        for item in category_rows:
            key = opportunity_cohort_key(item)
            cohort_key_by_id[item.external_id] = key
            if key:
                cohort_groups.setdefault(key, []).append(item)
            else:
                ungrouped_rates.append(item.views_per_hour)

        # Balance the category baseline by product family. Ten iPhones must not
        # count ten times more than one emerging drill/camera/coffee machine.
        balanced_category_rates = list(ungrouped_rates)
        for group in cohort_groups.values():
            balanced_category_rates.append(percentile_value([x.views_per_hour for x in group], 0.50))
        if len(balanced_category_rates) < 2:
            balanced_category_rates = list(category_rates)
        category_median = max(0.25, percentile_value(balanced_category_rates, 0.50))

        for row in category_rows:
            cohort_key = cohort_key_by_id.get(row.external_id, "")
            peers = cohort_groups.get(cohort_key, []) if cohort_key else []
            use_peers = len(peers) >= 3
            peer_rates = [x.views_per_hour for x in peers] if use_peers else category_rates
            peer_count = len(peer_rates)
            peer_median = percentile_value(peer_rates, 0.50)
            peer_p85 = percentile_value(peer_rates, 0.85)
            peer_p90 = percentile_value(peer_rates, 0.90)

            # Category percentile is the broad demand signal; peer stats are kept for
            # future checkpoints so a mass family has to outperform its own baseline too.
            category_velocity_pct = percentile_rank(row.views_per_hour, balanced_category_rates)
            views_pct = percentile_rank(float(row.views), view_values)
            absolute_demand = _absolute_demand_factor(row.views, row.views_per_hour)
            demand_factor = 0.76 * category_velocity_pct + 0.24 * absolute_demand

            category_ratio = row.views_per_hour / category_median
            if use_peers:
                peer_ratio = row.views_per_hour / max(0.25, peer_median)
                anomaly_factor = 0.72 * _ratio_factor(category_ratio) + 0.28 * _ratio_factor(peer_ratio)
            else:
                anomaly_factor = _ratio_factor(category_ratio)
            anomaly_ratio = category_ratio

            market_value = None
            # New keys are prefixed; raw identity lookup preserves compatibility with v4.5 tests/data.
            for key in (cohort_key, row.identity_key or "", (cohort_key[3:] if cohort_key.startswith("id:") else "")):
                if key and key in market_stats:
                    market_value = market_stats[key]
                    break
            market_median, market_count = _market_tuple(market_value)
            price_factor, price_delta = _price_factor(row.price_eur, market_median)
            supply_fit = _supply_fit(market_count)
            scan_share = (len(peers) / max(1, len(category_rows))) if cohort_key else 0.0
            mass_penalty = _mass_penalty(market_count, scan_share if len(peers) >= 8 and len(category_rows) >= 30 else 0.0)
            freshness = clamp(1.0 - (row.age_minutes / (24.0 * 60.0)))

            # Recognition confidence is *not* a score component. This is the key v2
            # correction: a Makita/coffee-machine/camera the parser does not know can
            # beat an iPhone if its demand/supply signal is stronger.
            raw = (
                36.0 * demand_factor
                + 28.0 * anomaly_factor
                + 16.0 * supply_fit
                + 8.0 * freshness
                + 4.0 * price_factor
                + 8.0 * (0.5 if not use_peers else clamp(percentile_rank(peer_median, balanced_category_rates)))
                - mass_penalty
            )
            score = int(round(max(0.0, min(100.0, raw))))

            identity_conf = int(row.identity_confidence or 0)
            evidence_count = market_count if market_count > 0 else (len(peers) if cohort_key else 0)
            confidence = int(round(max(25.0, min(96.0,
                42.0
                + min(22.0, max(0, peer_count - 1) * 2.0)
                + min(20.0, math.sqrt(max(0, evidence_count)) * 3.2)
                + (5.0 if cohort_key else 0.0)
                + min(5.0, identity_conf * 0.05)
            ))))

            uncertainty = max(0.16, min(0.36, 0.42 - 0.0025 * confidence))
            growth3 = row.views_per_hour * 3.0 * 0.92
            growth6 = row.views_per_hour * 6.0 * 0.84
            p3_low = max(row.views, int(round(row.views + growth3 * (1.0 - uncertainty))))
            p3_high = max(p3_low, int(round(row.views + growth3 * (1.0 + uncertainty))))
            p6_low = max(row.views, int(round(row.views + growth6 * (1.0 - uncertainty))))
            p6_high = max(p6_low, int(round(row.views + growth6 * (1.0 + uncertainty))))

            opp_type = _opportunity_type(market_count, mass_penalty, anomaly_ratio, len(peers) if cohort_key else 0)
            reasons: list[str] = []
            if opp_type == "hidden_gem":
                reasons.append("💎 Hidden Gem: сильный спрос при ограниченном предложении")
            elif opp_type == "emerging":
                reasons.append("🚀 Emerging: ниша показывает устойчивый спрос до массового насыщения")
            elif opp_type == "saturated":
                reasons.append("⚠️ Saturated: спрос есть, но ниша уже массовая — Score снижен")
            else:
                reasons.append("⚡ Anomaly: объявление заметно выбивается по спросу и требует подтверждения")

            if category_ratio >= 2.0:
                reasons.append(f"темп {category_ratio:.1f}× выше медианы категории")
            elif category_velocity_pct >= 0.85:
                reasons.append(f"темп входит примерно в верхние {max(1, 100-int(category_velocity_pct*100))}% категории")
            else:
                reasons.append("темп пока не даёт сильной аномалии относительно категории")

            if use_peers:
                reasons.append(f"есть {len(peers)} сопоставимых объявления в текущем скане")
            if market_count:
                reasons.append(f"за окно рынка найдено {market_count} сопоставимых публикаций")
            else:
                reasons.append("история этой товарной семьи ещё небольшая — решает реальный спрос, а не известность модели")
            if mass_penalty >= 5:
                reasons.append(f"штраф за массовость: −{mass_penalty:.0f} Score")
            elif 6 <= market_count <= 35:
                reasons.append("предложение находится в sweet spot: уже подтверждено, но ещё не массовое")
            elif 0 < market_count <= 2:
                reasons.append("слишком мало повторных публикаций: бонус за редкость ограничен")

            if price_delta is not None:
                if price_delta >= 8:
                    reasons.append(f"цена примерно на {price_delta:.0f}% ниже медианы похожих")
                elif price_delta <= -10:
                    reasons.append(f"цена примерно на {abs(price_delta):.0f}% выше медианы похожих")
                else:
                    reasons.append("цена близка к медиане похожих объявлений")
            if row.age_minutes <= 90:
                reasons.append("объявление очень свежее")

            result.append(InitialScore(
                external_id=row.external_id,
                score=score,
                confidence=confidence,
                stage=stage_for_score(score),
                views_per_hour=row.views_per_hour,
                velocity_percentile=category_velocity_pct,
                views_percentile=views_pct,
                peer_count=peer_count,
                peer_vph_median=peer_median,
                peer_vph_p85=peer_p85,
                peer_vph_p90=peer_p90,
                market_median_eur=float(market_median) if market_median is not None else None,
                market_cohort_size=int(market_count),
                price_delta_pct=price_delta,
                predicted_3h_low=p3_low,
                predicted_3h_high=p3_high,
                predicted_6h_low=p6_low,
                predicted_6h_high=p6_high,
                reasons=tuple(reasons),
                cohort_key=cohort_key,
                supply_fit=supply_fit,
                mass_penalty=mass_penalty,
                anomaly_ratio=anomaly_ratio,
                opportunity_type=opp_type,
            ))
    return result


def select_candidates(
    scores: list[InitialScore],
    category_by_external_id: dict[str, str],
    *,
    score_floor: int = 65,
    per_category: int = 10,
    total_limit: int = 20,
    control_per_category: int = 2,
    max_per_cohort: int = 2,
) -> tuple[list[str], set[str]]:
    """Bounded opportunity shortlist with diversity by product family."""
    by_category: dict[str, list[InitialScore]] = {}
    for score in scores:
        by_category.setdefault(category_by_external_id.get(score.external_id, "unknown"), []).append(score)

    selected: list[InitialScore] = []
    controls: set[str] = set()
    for rows in by_category.values():
        rows = sorted(rows, key=lambda x: (x.score, x.velocity_percentile), reverse=True)
        cohort_used: dict[str, int] = {}
        visible_count = 0
        for item in rows:
            if item.score < score_floor or visible_count >= max(1, per_category):
                continue
            key = item.cohort_key or f"single:{item.external_id}"
            if cohort_used.get(key, 0) >= max(1, max_per_cohort):
                continue
            cohort_used[key] = cohort_used.get(key, 0) + 1
            selected.append(item)
            visible_count += 1

        low = [x for x in rows if x.score < score_floor]
        for item in low[:max(0, control_per_category)]:
            selected.append(item)
            controls.add(item.external_id)

    dedup: dict[str, InitialScore] = {item.external_id: item for item in selected}
    ordered = sorted(dedup.values(), key=lambda x: (x.external_id in controls, -x.score))
    visible = [x for x in ordered if x.external_id not in controls][:max(1, total_limit)]
    control_rows = [x for x in ordered if x.external_id in controls]
    final = visible + control_rows
    return [x.external_id for x in final], controls


def update_dynamic_score(
    *,
    initial_score: int,
    initial_views_per_hour: float,
    baseline_views: int,
    current_views: int,
    elapsed_hours: float,
    peer_vph_median: float,
    peer_vph_p85: float,
    target_hours: int,
    previous_views: int | None = None,
    previous_elapsed_hours: float | None = None,
) -> DynamicScore:
    elapsed = max(0.25, float(elapsed_hours))
    delta = max(0, int(current_views) - int(baseline_views))
    observed_rate = float(delta) / elapsed

    spread = max(1.0, float(peer_vph_p85) - float(peer_vph_median))
    strength = clamp((observed_rate - float(peer_vph_median)) / spread)
    recent_rate = observed_rate
    if previous_views is not None and previous_elapsed_hours is not None:
        interval_hours = max(0.10, elapsed - max(0.0, float(previous_elapsed_hours)))
        interval_delta = max(0, int(current_views) - int(previous_views))
        recent_rate = float(interval_delta) / interval_hours

    if initial_views_per_hour <= 0:
        momentum = 1.0 if recent_rate > 0 else 0.0
    else:
        # After +1h, use the most recent interval (+1→+3, +3→+6) rather than
        # only the lifetime average. This is what lets the engine detect genuine
        # acceleration instead of merely rewarding an already-large baseline.
        momentum = recent_rate / max(0.5, float(initial_views_per_hour))
    momentum_factor = clamp((momentum - 0.50) / 1.00)

    score = int(round(max(0.0, min(100.0,
        float(initial_score) * 0.45 + strength * 37.0 + momentum_factor * 18.0
    ))))
    stage = stage_for_score(score)

    # Objective shadow-mode label. It is intentionally based on observed future
    # growth relative to the scan cohort, not on the model's own forecast.
    outcome = "pending"
    strong_threshold = max(2.0, float(peer_vph_p85))
    if target_hours >= 3 and delta >= 10 and observed_rate >= strong_threshold * 1.15 and score >= 88:
        outcome = "confirmed"
    elif target_hours >= 6:
        if delta >= 10 and observed_rate >= strong_threshold and score >= 84:
            outcome = "confirmed"
        else:
            outcome = "rejected"

    reasons: list[str] = []
    reasons.append(f"после старта +{delta} просмотров, средний темп {observed_rate:.1f}/ч")
    if previous_views is not None and previous_elapsed_hours is not None:
        reasons.append(f"темп последнего интервала {recent_rate:.1f}/ч")
    if observed_rate >= strong_threshold:
        reasons.append("фактический рост держится на уровне верхней части референсной группы")
    elif observed_rate <= max(1.0, peer_vph_median):
        reasons.append("фактический рост опустился к медиане референсной группы")
    if momentum >= 1.25:
        reasons.append("темп ускоряется относительно стартовой скорости")
    elif momentum < 0.75:
        reasons.append("темп замедляется относительно стартовой скорости")

    return DynamicScore(
        score=score,
        stage=stage,
        observed_views_per_hour=observed_rate,
        recent_views_per_hour=recent_rate,
        momentum_ratio=momentum,
        strength=strength,
        outcome=outcome,
        reasons=tuple(reasons),
    )
