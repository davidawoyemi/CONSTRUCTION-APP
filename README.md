# Construction Cost Estimator (MVP)

Website + backend MVP for a construction estimating app that:

- is currently calibrated for Nigeria-first costing (NGN and Nigeria region multipliers)
- accepts drawing files (PDF/TXT) and auto-generates takeoff quantities
- stores project takeoff items
- blends multiple price sources (supplier quotes + cost DB observations)
- applies regional multipliers and waste
- returns line-item estimate totals with freshness, confidence, and low/expected/high ranges
- supports locking estimate versions for bid snapshots
- uses benchmark-assisted calibration so totals stay realistic when drawing text is sparse

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
Website UI: `http://localhost:8000/`

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
- `POST /projects/{project_id}/drawings/auto-estimate` (drawing-first workflow)
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

### Drawing-first behavior

1. Upload drawing file (PDF or TXT) to `/projects/{project_id}/drawings/auto-estimate`.
2. App infers project assumptions (area, floors, bedroom/bath count).
3. Optional assumption overrides can be supplied (`floor_area_sqm`, `floors`, `bedrooms`, `bathrooms`, `quality_level`).
4. App auto-generates major material requirements (cement, rebar, 6in/9in blocks, sand, paint, electrical, plumbing, roof timber/sheet, doors, windows, tiles, wires, pipes).
5. App returns a locked material list where users can enter known unit prices.
6. If you know prices, pass them in `known_prices_json` (key-value map by `material_key`).
7. You can add extra materials not detected from drawing via `additional_materials_json`.
8. Missing prices are filled with market-derived low/expected/high ranges.
9. Response includes total low/expected/high project cost and missing material keys where user prices can improve accuracy.
10. If extracted quantities look under-scoped, the app adds a benchmark adjustment line for labor/preliminaries/overhead.

### Nigeria-first pricing defaults

- Currency: NGN (Naira)
- Regional multipliers: Nigeria national + Lagos/Abuja/Port Harcourt/Kano adjustments
- Seeded market references and invoice references are Nigeria-oriented
- Architecture still supports adding other countries/regions later

## Running tests

```bash
pytest -q
```

## Deploy for a permanent URL (Render)

This repo includes:
- `Dockerfile`
- `render.yaml`

Deploy steps:
1. Push branch to GitHub.
2. In Render, create a new Blueprint from this repo.
3. Render will deploy `buildsmart-estimator` using Docker.
4. Use the generated `*.onrender.com` URL as an always-on endpoint (depends on Render plan).

## Free hosting options (no card)

If Render asks for payment, use one of these no-card options:

1) Hugging Face Spaces (Docker Space)
- Create a new Space: `Docker` SDK
- Point it to this repository/branch
- It will use the `Dockerfile` and expose port `7860`
- You get a shareable URL like `https://<space-name>.hf.space`

2) Railway/other hosts
- Ensure host supports Docker and dynamic `PORT`
- This repo is already configured to read `PORT` from environment
