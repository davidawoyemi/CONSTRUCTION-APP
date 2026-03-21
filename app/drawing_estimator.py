from __future__ import annotations

import math
import re
from dataclasses import dataclass


def normalize_material_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "custom_material"


@dataclass
class Assumptions:
    floor_area_sqm: float
    floors: int
    bedrooms: int
    bathrooms: int
    roof_factor: float


def apply_assumption_overrides(assumptions: Assumptions, overrides: dict | None) -> Assumptions:
    if not overrides:
        return assumptions

    floor_area_sqm = assumptions.floor_area_sqm
    floors = assumptions.floors
    bedrooms = assumptions.bedrooms
    bathrooms = assumptions.bathrooms
    roof_factor = assumptions.roof_factor

    if "floor_area_sqm" in overrides and overrides["floor_area_sqm"] is not None:
        floor_area_sqm = max(30.0, float(overrides["floor_area_sqm"]))
    if "floors" in overrides and overrides["floors"] is not None:
        floors = max(1, int(overrides["floors"]))
    if "bedrooms" in overrides and overrides["bedrooms"] is not None:
        bedrooms = max(1, int(overrides["bedrooms"]))
    if "bathrooms" in overrides and overrides["bathrooms"] is not None:
        bathrooms = max(1, int(overrides["bathrooms"]))
    if "roof_factor" in overrides and overrides["roof_factor"] is not None:
        roof_factor = max(0.8, min(float(overrides["roof_factor"]), 2.0))

    return Assumptions(
        floor_area_sqm=round(floor_area_sqm, 2),
        floors=floors,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        roof_factor=round(roof_factor, 2),
    )


def infer_assumptions(drawing_text: str) -> Assumptions:
    text = drawing_text.lower()

    floor_area_sqm = 180.0
    sqm_match = re.search(r"(\d+(?:\.\d+)?)\s*(sqm|sq\s?m|m2|m\^2)", text)
    sqft_match = re.search(r"(\d+(?:\.\d+)?)\s*(sqft|sq\s?ft|ft2|ft\^2)", text)
    if sqm_match:
        floor_area_sqm = float(sqm_match.group(1))
    elif sqft_match:
        floor_area_sqm = float(sqft_match.group(1)) * 0.092903

    floors = 1
    floor_match = re.search(r"(\d+)\s*(storey|story|floor|floors)", text)
    if floor_match:
        floors = max(1, int(floor_match.group(1)))

    bedrooms = max(2, math.ceil(floor_area_sqm / 55))
    bed_match = re.search(r"(\d+)\s*(bedroom|bedrooms|bed)", text)
    if bed_match:
        bedrooms = max(1, int(bed_match.group(1)))

    bathrooms = max(1, math.ceil(bedrooms * 0.7))
    bath_match = re.search(r"(\d+)\s*(bathroom|bathrooms|bath|wc)", text)
    if bath_match:
        bathrooms = max(1, int(bath_match.group(1)))

    roof_factor = 1.25
    if "flat roof" in text:
        roof_factor = 1.05
    elif "gable roof" in text or "pitched roof" in text:
        roof_factor = 1.3

    return Assumptions(
        floor_area_sqm=round(floor_area_sqm, 2),
        floors=floors,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        roof_factor=roof_factor,
    )


def generate_material_requirements(assumptions: Assumptions) -> list[dict]:
    area = assumptions.floor_area_sqm
    floors = assumptions.floors
    bedrooms = assumptions.bedrooms
    bathrooms = assumptions.bathrooms
    gross_area = area * floors
    roof_area = gross_area * assumptions.roof_factor

    electrical_points = (bedrooms * 9) + (bathrooms * 5) + (floors * 12)
    plumbing_points = (bedrooms * 4) + (bathrooms * 7) + (floors * 6)
    doors = bedrooms + bathrooms + (floors * 3)
    windows = (bedrooms * 2) + (floors * 4)

    materials = [
        {
            "material_key": "cement_bag",
            "item_name": "Portland Cement",
            "csi_code": "03-30-00",
            "unit": "bag",
            "quantity": max(120.0, gross_area * 0.24),
            "waste_factor_pct": 6.0,
        },
        {
            "material_key": "rebar_kg",
            "item_name": "Reinforcing Steel (Iron)",
            "csi_code": "03-20-00",
            "unit": "kg",
            "quantity": max(700.0, gross_area * 5.4),
            "waste_factor_pct": 5.0,
        },
        {
            "material_key": "block_6in",
            "item_name": "6-inch Concrete Block",
            "csi_code": "04-22-00",
            "unit": "ea",
            "quantity": gross_area * 6.5,
            "waste_factor_pct": 7.0,
        },
        {
            "material_key": "block_9in",
            "item_name": "9-inch Concrete Block",
            "csi_code": "04-22-10",
            "unit": "ea",
            "quantity": gross_area * 3.2,
            "waste_factor_pct": 7.0,
        },
        {
            "material_key": "sand_ton",
            "item_name": "Sharp Sand",
            "csi_code": "31-00-00",
            "unit": "ton",
            "quantity": gross_area * 0.18,
            "waste_factor_pct": 8.0,
        },
        {
            "material_key": "granite_ton",
            "item_name": "Granite/Stone Base",
            "csi_code": "32-12-00",
            "unit": "ton",
            "quantity": gross_area * 0.22,
            "waste_factor_pct": 8.0,
        },
        {
            "material_key": "paint_liter",
            "item_name": "Paint",
            "csi_code": "09-90-00",
            "unit": "liter",
            "quantity": (gross_area * 0.42) + (bedrooms * 8.0),
            "waste_factor_pct": 10.0,
        },
        {
            "material_key": "electrical_points",
            "item_name": "Electrical Points",
            "csi_code": "26-00-00",
            "unit": "ea",
            "quantity": electrical_points,
            "waste_factor_pct": 5.0,
        },
        {
            "material_key": "plumbing_points",
            "item_name": "Plumbing Points",
            "csi_code": "22-00-00",
            "unit": "ea",
            "quantity": plumbing_points,
            "waste_factor_pct": 5.0,
        },
        {
            "material_key": "roof_timber_m3",
            "item_name": "Roof Timber",
            "csi_code": "06-15-00",
            "unit": "m3",
            "quantity": roof_area * 0.018,
            "waste_factor_pct": 10.0,
        },
        {
            "material_key": "roof_sheet_sqm",
            "item_name": "Roof Sheet",
            "csi_code": "07-41-00",
            "unit": "sqm",
            "quantity": roof_area * 1.05,
            "waste_factor_pct": 6.0,
        },
        {
            "material_key": "doors_ea",
            "item_name": "Doors",
            "csi_code": "08-11-00",
            "unit": "ea",
            "quantity": float(doors),
            "waste_factor_pct": 3.0,
        },
        {
            "material_key": "windows_ea",
            "item_name": "Windows",
            "csi_code": "08-50-00",
            "unit": "ea",
            "quantity": float(windows),
            "waste_factor_pct": 3.0,
        },
        {
            "material_key": "tiles_sqm",
            "item_name": "Floor/Wall Tiles",
            "csi_code": "09-30-00",
            "unit": "sqm",
            "quantity": gross_area * 0.58,
            "waste_factor_pct": 8.0,
        },
        {
            "material_key": "plumbing_pipe_m",
            "item_name": "Plumbing Pipes",
            "csi_code": "22-11-00",
            "unit": "m",
            "quantity": (bedrooms + bathrooms) * 12.0 + (floors * 20.0),
            "waste_factor_pct": 6.0,
        },
        {
            "material_key": "electrical_wire_m",
            "item_name": "Electrical Wire",
            "csi_code": "26-05-00",
            "unit": "m",
            "quantity": electrical_points * 14.0,
            "waste_factor_pct": 7.0,
        },
    ]

    for row in materials:
        row["quantity"] = round(max(1.0, row["quantity"]), 2)

    return materials
