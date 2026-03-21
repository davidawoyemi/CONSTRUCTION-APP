from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any

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


def price_candidates_for_csi(
    db: Session,
    project: models.Project,
    csi_code: str,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    region_code = region_from_zip(project.zip_code)
    candidates: list[dict[str, Any]] = []

    quotes = db.scalars(
        select(models.SupplierQuote).where(
            models.SupplierQuote.project_id == project.id,
            models.SupplierQuote.csi_code == csi_code,
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
        select(models.PriceCatalogItem).where(models.PriceCatalogItem.csi_code == csi_code)
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


def estimate_unit_price_range(
    db: Session,
    project: models.Project,
    csi_code: str,
    known_unit_price: float | None = None,
) -> dict[str, Any]:
    if known_unit_price is not None and known_unit_price > 0:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return {
            "unit_price_low": round(known_unit_price, 2),
            "unit_price_high": round(known_unit_price, 2),
            "unit_price_expected": round(known_unit_price, 2),
            "freshness_status": "user-provided",
            "confidence_score": 98.0,
            "price_source": "user_provided",
            "last_price_update_at": now,
            "source_trace_json": {
                "candidate_count": 1,
                "sources": [
                    {
                        "name": "User Input",
                        "type": "user_provided",
                        "unit_cost": known_unit_price,
                        "observed_at": now.isoformat(),
                    }
                ],
            },
        }

    candidates = price_candidates_for_csi(db, project, csi_code)
    if not candidates:
        fallback = 1.0
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return {
            "unit_price_low": round(fallback * 0.8, 2),
            "unit_price_high": round(fallback * 1.2, 2),
            "unit_price_expected": round(fallback, 2),
            "freshness_status": "stale",
            "confidence_score": 25.0,
            "price_source": "fallback",
            "last_price_update_at": now,
            "source_trace_json": {
                "candidate_count": 0,
                "sources": [],
            },
        }

    weighted_sum = 0.0
    weight_total = 0.0
    unit_costs: list[float] = []
    reliability_scores: list[float] = []
    recency_scores: list[float] = []
    latest_observed = candidates[0]["observed_at"]
    preferred_source = "market_range"

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
        if candidate["source_type"] == "supplier":
            preferred_source = "supplier+market"

    blended_unit_cost = weighted_sum / weight_total if weight_total > 0 else mean(unit_costs)
    region_code = region_from_zip(project.zip_code)
    region_multiplier = _resolve_region_multiplier(db, region_code, csi_code)
    expected = blended_unit_cost * region_multiplier

    spread_ratio = 0.12
    if len(unit_costs) > 1 and expected > 0:
        spread_ratio = max(spread_ratio, (max(unit_costs) - min(unit_costs)) / expected)
    low = max(0.01, expected * (1 - (0.45 * spread_ratio)))
    high = expected * (1 + (0.55 * spread_ratio))

    source_quality = mean(reliability_scores)
    recency_value = mean(recency_scores)
    agreement_value = _agreement_score(unit_costs)
    confidence = (0.4 * source_quality + 0.35 * recency_value + 0.25 * agreement_value) * 100

    return {
        "unit_price_low": round(low, 2),
        "unit_price_high": round(high, 2),
        "unit_price_expected": round(expected, 2),
        "freshness_status": freshness_label(latest_observed),
        "confidence_score": round(confidence, 2),
        "price_source": preferred_source,
        "last_price_update_at": latest_observed,
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


def price_takeoff_item(
    db: Session,
    project: models.Project,
    takeoff_item: models.TakeoffItem,
) -> dict:
    range_data = estimate_unit_price_range(db, project, takeoff_item.csi_code)
    if range_data["price_source"] == "fallback":
        raise ValueError(f"No price candidates for CSI {takeoff_item.csi_code}")

    final_unit_cost = range_data["unit_price_expected"] * (1 + (takeoff_item.waste_factor_pct / 100))
    subtotal_cost = final_unit_cost * takeoff_item.quantity

    return {
        "unit_cost_selected": round(final_unit_cost, 2),
        "subtotal_cost": round(subtotal_cost, 2),
        "last_price_update_at": range_data["last_price_update_at"],
        "freshness_status": range_data["freshness_status"],
        "confidence_score": range_data["confidence_score"],
        "source_trace_json": range_data["source_trace_json"],
    }


def seed_demo_prices(db: Session) -> None:
    source_by_name: dict[str, models.PriceSource] = {}
    for source in db.scalars(select(models.PriceSource)).all():
        source_by_name[source.name] = source

    if "National Cost DB" not in source_by_name:
        db.add(models.PriceSource(source_type="cost_db", name="National Cost DB", reliability_score=0.78))
    if "Historical Invoices" not in source_by_name:
        db.add(
            models.PriceSource(
                source_type="historical_invoice",
                name="Historical Invoices",
                reliability_score=0.72,
            )
        )
    db.flush()
    for source in db.scalars(select(models.PriceSource)).all():
        source_by_name[source.name] = source

    catalog_specs = [
        ("09-29-00", "Gypsum Board", "sqft", "material"),
        ("06-11-00", "Wood Framing", "lf", "material"),
        ("03-30-00", "Portland Cement", "bag", "material"),
        ("03-20-00", "Reinforcing Steel", "kg", "material"),
        ("04-22-00", "6-inch Concrete Block", "ea", "material"),
        ("04-22-10", "9-inch Concrete Block", "ea", "material"),
        ("31-00-00", "Sharp Sand", "ton", "material"),
        ("32-12-00", "Granite/Stone Base", "ton", "material"),
        ("09-90-00", "Paint", "liter", "material"),
        ("26-00-00", "Electrical Points", "ea", "labor"),
        ("22-00-00", "Plumbing Points", "ea", "labor"),
        ("07-41-00", "Roof Sheet", "sqm", "material"),
        ("08-11-00", "Doors", "ea", "material"),
        ("08-50-00", "Windows", "ea", "material"),
        ("09-30-00", "Floor/Wall Tiles", "sqm", "material"),
        ("22-11-00", "Plumbing Pipes", "m", "material"),
        ("26-05-00", "Electrical Wire", "m", "material"),
        ("06-15-00", "Roof Timber", "m3", "material"),
    ]
    catalog_index: dict[str, models.PriceCatalogItem] = {}
    existing_catalog = db.scalars(select(models.PriceCatalogItem)).all()
    for item in existing_catalog:
        catalog_index[f"{item.csi_code}|{item.item_name}"] = item

    for csi_code, item_name, unit, category in catalog_specs:
        key = f"{csi_code}|{item_name}"
        if key not in catalog_index:
            db.add(
                models.PriceCatalogItem(
                    csi_code=csi_code,
                    item_name=item_name,
                    default_unit=unit,
                    category=category,
                )
            )
    db.flush()
    existing_catalog = db.scalars(select(models.PriceCatalogItem)).all()
    for item in existing_catalog:
        catalog_index[f"{item.csi_code}|{item.item_name}"] = item

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    obs_index = {
        (obs.price_catalog_item_id, obs.source_id, obs.region_code): obs
        for obs in db.scalars(select(models.PriceObservation)).all()
    }
    source_cost_db = source_by_name["National Cost DB"]
    source_invoice = source_by_name["Historical Invoices"]

    observation_specs = [
        ("09-29-00", "Gypsum Board", 2.2, 2.1),
        ("06-11-00", "Wood Framing", 14.0, 13.4),
        ("03-30-00", "Portland Cement", 10.5, 9.9),
        ("03-20-00", "Reinforcing Steel", 1.45, 1.36),
        ("04-22-00", "6-inch Concrete Block", 1.35, 1.28),
        ("04-22-10", "9-inch Concrete Block", 1.85, 1.72),
        ("31-00-00", "Sharp Sand", 38.0, 35.5),
        ("32-12-00", "Granite/Stone Base", 44.0, 41.2),
        ("09-90-00", "Paint", 7.8, 7.3),
        ("26-00-00", "Electrical Points", 42.0, 39.0),
        ("22-00-00", "Plumbing Points", 55.0, 51.0),
        ("07-41-00", "Roof Sheet", 16.5, 15.1),
        ("08-11-00", "Doors", 210.0, 195.0),
        ("08-50-00", "Windows", 180.0, 165.0),
        ("09-30-00", "Floor/Wall Tiles", 12.5, 11.4),
        ("22-11-00", "Plumbing Pipes", 4.6, 4.2),
        ("26-05-00", "Electrical Wire", 1.75, 1.61),
        ("06-15-00", "Roof Timber", 590.0, 545.0),
    ]

    for csi_code, item_name, cost_db_value, invoice_value in observation_specs:
        catalog_item = catalog_index.get(f"{csi_code}|{item_name}")
        if not catalog_item:
            continue
        for source, value in ((source_cost_db, cost_db_value), (source_invoice, invoice_value)):
            key = (catalog_item.id, source.id, "US")
            if key not in obs_index:
                db.add(
                    models.PriceObservation(
                        price_catalog_item_id=catalog_item.id,
                        source_id=source.id,
                        region_code="US",
                        unit_cost=value,
                        observed_at=now,
                    )
                )

    multiplier_index = {
        (row.region_code, row.csi_code): row
        for row in db.scalars(select(models.RegionMultiplier)).all()
    }
    for csi_code, _, _, _ in catalog_specs:
        key = ("US", csi_code)
        if key not in multiplier_index:
            db.add(models.RegionMultiplier(region_code="US", csi_code=csi_code, multiplier=1.0))

    db.commit()
