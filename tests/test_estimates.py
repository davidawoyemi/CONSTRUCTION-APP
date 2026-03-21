import os
from pathlib import Path
import json

from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///./test_construction_app.db"

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.pricing import seed_demo_prices  # noqa: E402


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        seed_demo_prices(db)
    finally:
        db.close()


def teardown_module() -> None:
    db_file = Path("test_construction_app.db")
    if db_file.exists():
        db_file.unlink()


def test_index_page_loads() -> None:
    reset_db()
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "BuildSmart Estimator" in response.text


def test_drawing_auto_estimate_with_known_prices() -> None:
    reset_db()
    with TestClient(app) as client:
        project = client.post(
            "/projects",
            json={"name": "Auto Draw", "project_type": "residential", "zip_code": "30301"},
        )
        project_id = project.json()["id"]

        drawing_text = "Proposed 2 floor house, 240 sqm, 4 bedrooms, 3 bathrooms, pitched roof."
        known_prices = {"cement_bag": 11.2, "doors_ea": 240}
        response = client.post(
            f"/projects/{project_id}/drawings/auto-estimate",
            files={"drawing": ("sample.txt", drawing_text.encode("utf-8"), "text/plain")},
            data={"known_prices_json": json.dumps(known_prices)},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["assumptions"]["floors"] == 2
        assert payload["assumptions"]["quality_level"] == "standard"
        assert len(payload["materials"]) >= 12
        assert payload["totals"]["total_expected"] > 0
        assert payload["benchmark_totals"]["total_expected"] > 0

        cement = [row for row in payload["materials"] if row["material_key"] == "cement_bag"][0]
        assert cement["price_source"] == "user_provided"
        assert cement["unit_price_expected"] == 11.2
        assert "rebar_kg" in payload["missing_unit_price_items"]


def test_drawing_auto_estimate_supports_additional_materials() -> None:
    reset_db()
    with TestClient(app) as client:
        project = client.post(
            "/projects",
            json={"name": "Extra Material Build", "project_type": "residential", "zip_code": "60601"},
        )
        project_id = project.json()["id"]

        extra_materials = [
            {
                "item_name": "Waterproof Membrane",
                "csi_code": "07-13-00",
                "quantity": 120,
                "unit": "sqm",
                "waste_factor_pct": 5,
                "known_unit_price": 6.8,
            }
        ]

        response = client.post(
            f"/projects/{project_id}/drawings/auto-estimate",
            files={"drawing": ("sample.txt", b"Single floor house 150 sqm", "text/plain")},
            data={"additional_materials_json": json.dumps(extra_materials)},
        )
        assert response.status_code == 200
        payload = response.json()
        extra_row = [row for row in payload["materials"] if row["item_name"] == "Waterproof Membrane"][0]
        assert extra_row["price_source"] == "user_provided"
        assert extra_row["unit_price_expected"] == 6.8


def test_estimate_reprice_and_lock() -> None:
    reset_db()
    with TestClient(app) as client:
        project = client.post(
            "/projects",
            json={"name": "Demo Build", "project_type": "residential", "zip_code": "77001"},
        )
        assert project.status_code == 201
        project_id = project.json()["id"]

        takeoff = client.post(
            f"/projects/{project_id}/takeoff-items",
            json={
                "csi_code": "09-29-00",
                "item_name": "Drywall Main Floor",
                "quantity": 1200,
                "unit": "sqft",
                "waste_factor_pct": 8,
            },
        )
        assert takeoff.status_code == 201

        estimate = client.post(
            f"/projects/{project_id}/estimate-versions",
            json={"version_name": "v1 base estimate"},
        )
        assert estimate.status_code == 201
        estimate_id = estimate.json()["id"]

        repriced = client.post(f"/estimate-versions/{estimate_id}/reprice")
        assert repriced.status_code == 200
        data = repriced.json()
        assert data["is_locked"] is False
        assert len(data["line_items"]) == 1
        assert data["line_items"][0]["unit_cost_selected"] > 0
        assert data["line_items"][0]["confidence_score"] > 0

        locked = client.post(f"/estimate-versions/{estimate_id}/lock")
        assert locked.status_code == 200
        assert locked.json()["is_locked"] is True

        second_reprice = client.post(f"/estimate-versions/{estimate_id}/reprice")
        assert second_reprice.status_code == 409


def test_supplier_quote_impacts_pricing() -> None:
    reset_db()
    with TestClient(app) as client:
        project = client.post(
            "/projects",
            json={"name": "Drywall Office", "project_type": "commercial", "zip_code": "10001"},
        )
        project_id = project.json()["id"]

        client.post(
            f"/projects/{project_id}/takeoff-items",
            json={
                "csi_code": "09-29-00",
                "item_name": "Office Drywall",
                "quantity": 100,
                "unit": "sqft",
                "waste_factor_pct": 0,
            },
        )

        estimate = client.post(
            f"/projects/{project_id}/estimate-versions",
            json={"version_name": "supplier test"},
        )
        estimate_id = estimate.json()["id"]

        baseline = client.post(f"/estimate-versions/{estimate_id}/reprice").json()
        baseline_cost = baseline["line_items"][0]["unit_cost_selected"]

        quote_import = client.post(
            f"/projects/{project_id}/supplier-quotes/import",
            json=[
                {
                    "supplier_name": "ACME Supply",
                    "quote_ref": "Q-443",
                    "csi_code": "09-29-00",
                    "item_name": "Gypsum Board",
                    "unit": "sqft",
                    "quoted_unit_cost": 15000.0,
                }
            ],
        )
        assert quote_import.status_code == 201

        repriced = client.post(f"/estimate-versions/{estimate_id}/reprice")
        assert repriced.status_code == 200
        new_cost = repriced.json()["line_items"][0]["unit_cost_selected"]
        assert new_cost > baseline_cost
