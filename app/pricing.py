from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models


SOURCE_TYPE_WEIGHTS = {
    "supplier": 1.0,
    "cost_db": 0.8,
    "historical_invoice": 0.6,
    "manual": 0.4,
}


def region_from_zip(zip_code: str) -> str:
    clean = "".join(char for char in zip_code if char.isdigit())
    return clean[:3] if len(clean) >= 3 else "US"


def recency_score(observed_at: datetime) -> float:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    age_days = max((now - observed_at).days, 0)
    if age_days <= 14:
        return 1.0
    if age_days <= 30:
        return 0.7
    if age_days <= 60:
        return 0.45
    return 0.2


def freshness_label(observed_at: datetime) -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    age_days = max((now - observed_at).days, 0)
    if age_days <= 14:
        return "fresh"
    if age_days <= 30:
        return "aging"
    return "stale"


def _agreement_score(unit_costs: list[float]) -> float:
    if len(unit_costs) == 1:
        return 1.0
    avg = mean(unit_costs)
    if avg == 0:
        return 0.2
    spread_ratio = (max(unit_costs) - min(unit_costs)) / avg
    if spread_ratio <= 0.1:
        return 1.0
    if spread_ratio <= 0.25:
        return 0.8
    if spread_ratio <= 0.5:
        return 0.55
    return 0.3


def _resolve_region_multiplier(db: Session, region_code: str, csi_code: str) -> float:
    multiplier = db.scalar(
        select(models.RegionMultiplier.multiplier).where(
            models.RegionMultiplier.region_code == region_code,
            models.RegionMultiplier.csi_code == csi_code,
        )
    )
    if multiplier is not None:
        return multiplier

    national = db.scalar(
        select(models.RegionMultiplier.multiplier).where(
            models.RegionMultiplier.region_code == "US",
            models.RegionMultiplier.csi_code == csi_code,
        )
    )
    return national if national is not None else 1.0


def _candidate_rows(
    db: Session,
    project: models.Project,
    takeoff_item: models.TakeoffItem,
) -> list[dict]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    region_code = region_from_zip(project.zip_code)
    candidates: list[dict] = []

    quotes = db.scalars(
        select(models.SupplierQuote).where(
            models.SupplierQuote.project_id == project.id,
            models.SupplierQuote.csi_code == takeoff_item.csi_code,
        )
    ).all()

    for quote in quotes:
        if quote.valid_until and quote.valid_until < now:
            continue
        candidates.append(
            {
                "unit_cost": quote.quoted_unit_cost,
                "source_name": quote.supplier_name,
                "source_type": "supplier",
                "reliability": 0.95,
                "observed_at": quote.imported_at,
            }
        )

    catalog_items = db.scalars(
        select(models.PriceCatalogItem).where(models.PriceCatalogItem.csi_code == takeoff_item.csi_code)
    ).all()

    for catalog_item in catalog_items:
        observations = db.scalars(
            select(models.PriceObservation).where(
                models.PriceObservation.price_catalog_item_id == catalog_item.id,
                models.PriceObservation.region_code.in_([region_code, "US"]),
            )
        ).all()
        for observation in observations:
            candidates.append(
                {
                    "unit_cost": observation.unit_cost,
                    "source_name": observation.source.name,
                    "source_type": observation.source.source_type,
                    "reliability": observation.source.reliability_score,
                    "observed_at": observation.observed_at,
                }
            )

    return candidates


def price_takeoff_item(
    db: Session,
    project: models.Project,
    takeoff_item: models.TakeoffItem,
) -> dict:
    candidates = _candidate_rows(db, project, takeoff_item)
    if not candidates:
        raise ValueError(f"No price candidates for CSI {takeoff_item.csi_code}")

    weighted_sum = 0.0
    weight_total = 0.0
    unit_costs: list[float] = []
    reliability_scores: list[float] = []
    recency_scores: list[float] = []
    latest_observed = candidates[0]["observed_at"]

    for candidate in candidates:
        source_weight = SOURCE_TYPE_WEIGHTS.get(candidate["source_type"], 0.4)
        recency = recency_score(candidate["observed_at"])
        reliability = max(min(candidate["reliability"], 1.0), 0.0)
        combined_weight = source_weight * recency * reliability
        weighted_sum += candidate["unit_cost"] * combined_weight
        weight_total += combined_weight
        unit_costs.append(candidate["unit_cost"])
        reliability_scores.append(reliability)
        recency_scores.append(recency)
        if candidate["observed_at"] > latest_observed:
            latest_observed = candidate["observed_at"]

    blended_unit_cost = weighted_sum / weight_total if weight_total > 0 else mean(unit_costs)
    region_code = region_from_zip(project.zip_code)
    region_multiplier = _resolve_region_multiplier(db, region_code, takeoff_item.csi_code)

    final_unit_cost = blended_unit_cost * region_multiplier * (1 + (takeoff_item.waste_factor_pct / 100))
    subtotal_cost = final_unit_cost * takeoff_item.quantity

    source_quality = mean(reliability_scores)
    recency_value = mean(recency_scores)
    agreement_value = _agreement_score(unit_costs)
    confidence = (0.4 * source_quality + 0.35 * recency_value + 0.25 * agreement_value) * 100

    return {
        "unit_cost_selected": round(final_unit_cost, 2),
        "subtotal_cost": round(subtotal_cost, 2),
        "last_price_update_at": latest_observed,
        "freshness_status": freshness_label(latest_observed),
        "confidence_score": round(confidence, 2),
        "source_trace_json": {
            "candidate_count": len(candidates),
            "region_multiplier": region_multiplier,
            "blended_unit_cost": round(blended_unit_cost, 4),
            "sources": [
                {
                    "name": candidate["source_name"],
                    "type": candidate["source_type"],
                    "unit_cost": candidate["unit_cost"],
                    "observed_at": candidate["observed_at"].isoformat(),
                }
                for candidate in candidates
            ],
        },
    }


def seed_demo_prices(db: Session) -> None:
    existing = db.scalar(select(models.PriceSource.id))
    if existing:
        return

    cost_db_source = models.PriceSource(source_type="cost_db", name="National Cost DB", reliability_score=0.78)
    invoice_source = models.PriceSource(
        source_type="historical_invoice",
        name="Historical Invoices",
        reliability_score=0.72,
    )
    db.add_all([cost_db_source, invoice_source])
    db.flush()

    drywall = models.PriceCatalogItem(
        csi_code="09-29-00",
        item_name="Gypsum Board",
        default_unit="sqft",
        category="material",
    )
    framing = models.PriceCatalogItem(
        csi_code="06-11-00",
        item_name="Wood Framing",
        default_unit="lf",
        category="material",
    )
    db.add_all([drywall, framing])
    db.flush()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add_all(
        [
            models.PriceObservation(
                price_catalog_item_id=drywall.id,
                source_id=cost_db_source.id,
                region_code="US",
                unit_cost=2.2,
                observed_at=now,
            ),
            models.PriceObservation(
                price_catalog_item_id=drywall.id,
                source_id=invoice_source.id,
                region_code="US",
                unit_cost=2.1,
                observed_at=now,
            ),
            models.PriceObservation(
                price_catalog_item_id=framing.id,
                source_id=cost_db_source.id,
                region_code="US",
                unit_cost=14.0,
                observed_at=now,
            ),
            models.PriceObservation(
                price_catalog_item_id=framing.id,
                source_id=invoice_source.id,
                region_code="US",
                unit_cost=13.4,
                observed_at=now,
            ),
            models.RegionMultiplier(region_code="US", csi_code="09-29-00", multiplier=1.0),
            models.RegionMultiplier(region_code="US", csi_code="06-11-00", multiplier=1.0),
        ]
    )
    db.commit()
