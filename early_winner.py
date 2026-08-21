from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from product_identity import canonical_text
from zoneinfo import ZoneInfo

MODEL_VERSION = "product-opportunity-v3"
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
    # v4.6: popularity/saturation is descriptive, never a direct negative Score.
    saturation_score: int = 0
    supply_percentile: float = 0.0
    supply_growth_ratio: float = 1.0
    demand_growth_ratio: float = 1.0
    demand_supply_ratio: float = 1.0
    repeatability: float = 0.0
    anomaly_ratio: float = 1.0
    current_vs_history_ratio: float = 1.0
    opportunity_type: str = "spark"
    # Kept as compatibility aliases for older tests/callers. v4.6 never subtracts them.
    supply_fit: float = 0.0
    mass_penalty: float = 0.0


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


def _market_profile(value) -> dict[str, float | int | None]:
    """Normalize legacy v4.5 stats and the richer v4.6 market profile.

    Saturation and trend are evidence, not a veto. Missing history is deliberately
    neutral for opportunity scoring and lowers confidence instead of awarding rarity.
    """
    profile: dict[str, float | int | None] = {
        "median": None,
        "count": 0,
        "supply_percentile": 0.0,
        "recent_count": 0,
        "previous_count": 0,
        "supply_growth_ratio": 1.0,
        "demand_recent_median": None,
        "demand_previous_median": None,
        "demand_growth_ratio": 1.0,
        "demand_recent_samples": 0,
        "demand_previous_samples": 0,
        "prior_signals": 0,
        "prior_confirmed": 0,
    }
    if value is None:
        return profile
    if isinstance(value, tuple) and len(value) >= 2:
        profile["median"] = float(value[0]) if value[0] is not None else None
        profile["count"] = int(value[1] or 0)
        return profile
    if isinstance(value, dict):
        for key in profile:
            if key in value and value.get(key) is not None:
                profile[key] = value.get(key)
        profile["median"] = float(profile["median"]) if profile["median"] is not None else None
        for key in ("count", "recent_count", "previous_count", "demand_recent_samples", "demand_previous_samples", "prior_signals", "prior_confirmed"):
            profile[key] = int(profile[key] or 0)
        for key in ("supply_percentile", "supply_growth_ratio", "demand_growth_ratio"):
            profile[key] = float(profile[key] or (1.0 if "growth" in key else 0.0))
        for key in ("demand_recent_median", "demand_previous_median"):
            if profile[key] is not None:
                profile[key] = float(profile[key])
    return profile


def _ratio_factor(ratio: float) -> float:
    # 0.5x -> 0; 1x -> .33; 2x -> .67; 4x+ -> 1.0.
    ratio = max(0.05, float(ratio))
    return clamp((math.log(ratio, 2.0) + 1.0) / 3.0)


def _growth_factor(ratio: float) -> float:
    """Trend factor with 1.0x as a true neutral 0.5."""
    ratio = max(0.20, min(5.0, float(ratio or 1.0)))
    return clamp(0.5 + math.log(ratio, 2.0) / 2.0)


def _absolute_demand_factor(views: int, vph: float) -> float:
    # Stops a tiny category where "3 views vs 1 view" from looking like a huge opportunity.
    vph_part = clamp(math.log1p(max(0.0, vph)) / math.log1p(30.0))
    views_part = clamp(math.log1p(max(0, int(views))) / math.log1p(120.0))
    return 0.72 * vph_part + 0.28 * views_part


def _saturation_score(supply_percentile: float, scan_share: float, market_count: int) -> int:
    """0..100 category-relative saturation.

    v4.5.1 used absolute count thresholds (e.g. 200 listings = mass market) for every
    category. v4.6 compares a family with *other families in its own category* and only
    uses current-scan share as a secondary hint. Saturation never subtracts Score.
    """
    if int(market_count or 0) <= 0:
        return 0
    historical = clamp(float(supply_percentile or 0.0))
    scan_component = clamp(float(scan_share or 0.0) / 0.25)
    return int(round(100.0 * clamp(0.86 * historical + 0.14 * scan_component)))


def _combine_growth(current_vs_history: float | None, historical_growth: float | None) -> tuple[float, bool]:
    values = [float(x) for x in (current_vs_history, historical_growth) if x is not None and x > 0]
    if not values:
        return 1.0, False
    # Geometric mean: a one-day spike can help, but persistent historical growth matters too.
    log_mean = sum(math.log(max(0.20, min(5.0, x))) for x in values) / len(values)
    return max(0.20, min(5.0, math.exp(log_mean))), True


def _opportunity_type(
    *,
    score: int,
    saturation_score: int,
    anomaly_ratio: float,
    demand_growth_ratio: float,
    demand_supply_ratio: float,
    repeatability: float,
    trend_evidence: bool,
) -> str:
    """Classify *why* a product is interesting instead of treating popularity as bad."""
    movement = max(float(demand_growth_ratio or 1.0), float(demand_supply_ratio or 1.0))
    if score >= 80 and saturation_score >= 65 and trend_evidence and movement >= 1.18:
        return "hot_product"
    if score >= 80 and saturation_score <= 45 and anomaly_ratio >= 1.45 and repeatability >= 0.42:
        return "hidden_gem"
    if score >= 80 and trend_evidence and demand_supply_ratio >= 1.12 and movement >= 1.18:
        return "emerging"
    if saturation_score >= 70 and (not trend_evidence or movement < 1.12):
        return "saturated"
    return "spark"


def score_initial_rows(
    rows: list[FeatureRow],
    market_stats: dict[str, tuple[float, int] | dict] | None = None,
) -> list[InitialScore]:
    """DT Product Opportunity Engine v3.

    Opportunity Score answers: "is something unusually strong happening with this
    product now?" Saturation is a separate axis. A popular family can therefore become
    a Hot Product when demand accelerates; it is not pushed down merely for being large.
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

        # Family-balanced baseline: 20 copies of one iPhone do not redefine the category.
        balanced_category_rates = list(ungrouped_rates)
        for group in cohort_groups.values():
            balanced_category_rates.append(percentile_value([x.views_per_hour for x in group], 0.50))
        if len(balanced_category_rates) < 2:
            balanced_category_rates = list(category_rates)
        category_median = max(0.25, percentile_value(balanced_category_rates, 0.50))
        category_p75 = max(category_median, percentile_value(balanced_category_rates, 0.75))

        for row in category_rows:
            cohort_key = cohort_key_by_id.get(row.external_id, "")
            peers = cohort_groups.get(cohort_key, []) if cohort_key else []
            use_peers = len(peers) >= 3
            peer_rates = [x.views_per_hour for x in peers] if use_peers else category_rates
            peer_count = len(peer_rates)
            peer_median = percentile_value(peer_rates, 0.50)
            peer_p85 = percentile_value(peer_rates, 0.85)
            peer_p90 = percentile_value(peer_rates, 0.90)

            category_velocity_pct = percentile_rank(row.views_per_hour, balanced_category_rates)
            views_pct = percentile_rank(float(row.views), view_values)
            absolute_demand = _absolute_demand_factor(row.views, row.views_per_hour)
            demand_factor = 0.68 * category_velocity_pct + 0.32 * absolute_demand

            category_ratio = row.views_per_hour / category_median
            peer_ratio = row.views_per_hour / max(0.25, peer_median) if use_peers else category_ratio

            market_value = None
            for key in (cohort_key, row.identity_key or "", (cohort_key[3:] if cohort_key.startswith("id:") else "")):
                if key and key in market_stats:
                    market_value = market_stats[key]
                    break
            profile = _market_profile(market_value)
            market_median = profile["median"]
            market_count = int(profile["count"] or 0)
            price_factor, price_delta = _price_factor(row.price_eur, float(market_median) if market_median is not None else None)

            scan_share = (len(peers) / max(1, len(category_rows))) if cohort_key else 0.0
            saturation = _saturation_score(float(profile["supply_percentile"] or 0.0), scan_share, market_count)

            # Repeatability: multiple independent strong listings beat a single viral ad.
            if peers:
                strong_peers = sum(1 for x in peers if x.views_per_hour >= category_p75)
                peer_share = strong_peers / max(1, len(peers))
                peer_depth = clamp((len(peers) - 1) / 4.0)
                current_repeatability = 0.65 * peer_share + 0.35 * peer_depth
            else:
                current_repeatability = 0.28
            prior_signals = int(profile["prior_signals"] or 0)
            prior_confirmed = int(profile["prior_confirmed"] or 0)
            if prior_signals > 0:
                historical_repeatability = prior_confirmed / max(1, prior_signals)
                repeatability = clamp(0.65 * current_repeatability + 0.35 * historical_repeatability)
            else:
                repeatability = clamp(current_repeatability)

            demand_recent = profile["demand_recent_median"]
            demand_previous = profile["demand_previous_median"]
            history_samples = int(profile["demand_recent_samples"] or 0) + int(profile["demand_previous_samples"] or 0)
            current_family_rate = percentile_value([x.views_per_hour for x in peers], 0.50) if len(peers) >= 2 else row.views_per_hour
            current_vs_history: float | None = None
            if demand_recent is not None and float(demand_recent) > 0 and int(profile["demand_recent_samples"] or 0) >= 2:
                current_vs_history = current_family_rate / max(0.25, float(demand_recent))
            historical_growth: float | None = None
            if demand_recent is not None and demand_previous is not None and float(demand_previous) > 0 and history_samples >= 4:
                historical_growth = float(demand_recent) / max(0.25, float(demand_previous))
            demand_growth_ratio, trend_evidence = _combine_growth(current_vs_history, historical_growth)

            supply_growth_ratio = max(0.25, min(4.0, float(profile["supply_growth_ratio"] or 1.0)))
            demand_supply_ratio = (
                max(0.25, min(4.0, demand_growth_ratio / max(0.50, supply_growth_ratio)))
                if trend_evidence else 1.0
            )

            # Own-history acceleration is crucial for popular products: an iPhone that is
            # merely always popular is not a new opportunity; one accelerating vs itself is.
            if current_vs_history is not None:
                anomaly_factor = (
                    0.42 * _ratio_factor(category_ratio)
                    + 0.18 * _ratio_factor(peer_ratio)
                    + 0.40 * _growth_factor(current_vs_history)
                )
                anomaly_ratio = max(category_ratio, current_vs_history)
            else:
                anomaly_factor = 0.72 * _ratio_factor(category_ratio) + 0.28 * _ratio_factor(peer_ratio)
                anomaly_ratio = category_ratio

            trend_factor = _growth_factor(demand_growth_ratio) if trend_evidence else 0.50
            imbalance_factor = _growth_factor(demand_supply_ratio) if trend_evidence else 0.50
            freshness = clamp(1.0 - (row.age_minutes / (24.0 * 60.0)))

            # No mass penalty. Saturation is intentionally absent from this formula.
            raw = (
                34.0 * demand_factor
                + 26.0 * anomaly_factor
                + 16.0 * trend_factor
                + 12.0 * imbalance_factor
                + 8.0 * repeatability
                + 3.0 * freshness
                + 1.0 * price_factor
            )
            score = int(round(max(0.0, min(100.0, raw))))

            identity_conf = int(row.identity_confidence or 0)
            evidence_count = market_count if market_count > 0 else len(peers)
            confidence = int(round(max(22.0, min(97.0,
                34.0
                + min(18.0, max(0, len(peers) - 1) * 2.5)
                + min(18.0, math.sqrt(max(0, evidence_count)) * 2.6)
                + min(14.0, history_samples * 1.4)
                + min(8.0, prior_signals * 1.2)
                + (3.0 if cohort_key else 0.0)
                + min(2.0, identity_conf * 0.02)
            ))))

            uncertainty = max(0.16, min(0.38, 0.44 - 0.0027 * confidence))
            growth3 = row.views_per_hour * 3.0 * 0.92
            growth6 = row.views_per_hour * 6.0 * 0.84
            p3_low = max(row.views, int(round(row.views + growth3 * (1.0 - uncertainty))))
            p3_high = max(p3_low, int(round(row.views + growth3 * (1.0 + uncertainty))))
            p6_low = max(row.views, int(round(row.views + growth6 * (1.0 - uncertainty))))
            p6_high = max(p6_low, int(round(row.views + growth6 * (1.0 + uncertainty))))

            opp_type = _opportunity_type(
                score=score,
                saturation_score=saturation,
                anomaly_ratio=anomaly_ratio,
                demand_growth_ratio=demand_growth_ratio,
                demand_supply_ratio=demand_supply_ratio,
                repeatability=repeatability,
                trend_evidence=trend_evidence,
            )
            reasons: list[str] = []
            if opp_type == "hidden_gem":
                reasons.append("💎 Скрытая находка: сильный спрос при относительно низком насыщении")
            elif opp_type == "emerging":
                reasons.append("🚀 Набирает обороты: спрос растёт быстрее предложения")
            elif opp_type == "hot_product":
                reasons.append("🔥 Горячий товар: товар популярный, но спрос ускорился относительно собственной нормы")
            elif opp_type == "saturated":
                reasons.append("⚫ Перенасыщен: предложений много, нового ускорения спроса пока нет")
            else:
                reasons.append("⚡ Первичный сигнал: необычный спрос замечен, но товарному сигналу ещё нужны подтверждения")

            if current_vs_history is not None:
                reasons.append(f"текущий темп семьи {current_vs_history:.2f}× к её недавней собственной норме")
            elif category_ratio >= 1.5:
                reasons.append(f"темп {category_ratio:.1f}× выше медианы категории")
            else:
                reasons.append("истории собственного темпа пока мало; используем категорийный baseline")

            if trend_evidence:
                reasons.append(f"динамика спроса {demand_growth_ratio:.2f}× · спрос/предложение {demand_supply_ratio:.2f}×")
            else:
                reasons.append("история тренда ещё накапливается — нейтрально, без бонуса за неизвестность")
            if market_count:
                reasons.append(f"рынок: {market_count} сопоставимых · насыщение {saturation}/100 относительно своей категории")
            else:
                reasons.append("товарная семья новая для базы: насыщение неизвестно, а не автоматически «редко = хорошо»")
            if len(peers) >= 2:
                reasons.append(f"повторяемость текущего сигнала: {repeatability*100:.0f}% на {len(peers)} сопоставимых")
            if supply_growth_ratio >= 1.15:
                reasons.append(f"предложение тоже растёт: {supply_growth_ratio:.2f}× к норме категории")
            elif supply_growth_ratio <= 0.85 and market_count:
                reasons.append(f"предложение растёт медленнее рынка: {supply_growth_ratio:.2f}×")
            if price_delta is not None:
                if price_delta >= 8:
                    reasons.append(f"цена примерно на {price_delta:.0f}% ниже медианы похожих")
                elif price_delta <= -10:
                    reasons.append(f"цена примерно на {abs(price_delta):.0f}% выше медианы похожих")

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
                market_cohort_size=market_count,
                price_delta_pct=price_delta,
                predicted_3h_low=p3_low,
                predicted_3h_high=p3_high,
                predicted_6h_low=p6_low,
                predicted_6h_high=p6_high,
                reasons=tuple(reasons),
                cohort_key=cohort_key,
                saturation_score=saturation,
                supply_percentile=float(profile["supply_percentile"] or 0.0),
                supply_growth_ratio=supply_growth_ratio,
                demand_growth_ratio=demand_growth_ratio,
                demand_supply_ratio=demand_supply_ratio,
                repeatability=repeatability,
                anomaly_ratio=anomaly_ratio,
                current_vs_history_ratio=float(current_vs_history or 1.0),
                opportunity_type=opp_type,
                supply_fit=0.0,
                mass_penalty=0.0,
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
    """Bounded product-opportunity shortlist with family diversity.

    Saturated/no-movement products are background, not primary discoveries. Hot Product
    is explicitly allowed even at very high saturation when its own demand is moving.
    """
    by_category: dict[str, list[InitialScore]] = {}
    for score in scores:
        by_category.setdefault(category_by_external_id.get(score.external_id, "unknown"), []).append(score)

    selected: list[InitialScore] = []
    controls: set[str] = set()
    for rows in by_category.values():
        rows = sorted(rows, key=lambda x: (x.score, x.repeatability, x.velocity_percentile), reverse=True)
        cohort_used: dict[str, int] = {}
        visible_count = 0
        for item in rows:
            if item.score < score_floor or visible_count >= max(1, per_category):
                continue
            if item.opportunity_type == "saturated" and item.score < 90:
                continue
            key = item.cohort_key or f"single:{item.external_id}"
            if cohort_used.get(key, 0) >= max(1, max_per_cohort):
                continue
            cohort_used[key] = cohort_used.get(key, 0) + 1
            selected.append(item)
            visible_count += 1

        selected_ids = {x.external_id for x in selected}
        low = [x for x in rows if x.external_id not in selected_ids and x.score < score_floor]
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
