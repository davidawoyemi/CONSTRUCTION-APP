# Construction Cost Estimator (MVP)

Backend MVP for a construction estimating app that:

- stores project takeoff items
- blends multiple price sources (supplier quotes + cost DB observations)
- applies regional multipliers and waste
- returns line-item estimate totals with freshness and confidence
- supports locking estimate versions for bid snapshots

## Tech stack

- FastAPI
- SQLAlchemy
- SQLite (default local DB)
- Pytest

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API docs: `http://localhost:8000/docs`

## Data model implemented

- `projects`
- `takeoff_items`
- `price_sources`
- `price_catalog_items`
- `price_observations`
- `region_multipliers`
- `labor_rates` (table present for next iteration)
- `estimate_versions`
- `estimate_line_items`
- `supplier_quotes`

## Core endpoints

- `POST /projects`
- `POST /projects/{project_id}/takeoff-items`
- `POST /projects/{project_id}/supplier-quotes/import`
- `POST /projects/{project_id}/estimate-versions`
- `POST /estimate-versions/{estimate_version_id}/reprice`
- `POST /estimate-versions/{estimate_version_id}/lock`
- `GET /estimate-versions/{estimate_version_id}`

## Pricing behavior (v1)

For each takeoff item:

1. Gather candidate prices from valid supplier quotes + catalog observations.
2. Weight by source type, recency, and reliability.
3. Blend to unit cost, then apply:
   - regional multiplier
   - waste factor
4. Return:
   - selected unit cost
   - subtotal
   - freshness (`fresh`, `aging`, `stale`)
   - confidence score (0-100)
   - source trace metadata

## Running tests

```bash
pytest -q
```
