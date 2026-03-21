import os
from pathlib import Path

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
        assert "Construction Cost Estimator" in response.text


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
                    "quoted_unit_cost": 3.2,
                }
            ],
        )
        assert quote_import.status_code == 201

        repriced = client.post(f"/estimate-versions/{estimate_id}/reprice")
        assert repriced.status_code == 200
        new_cost = repriced.json()["line_items"][0]["unit_cost_selected"]
        assert new_cost > baseline_cost
