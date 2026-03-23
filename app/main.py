from contextlib import asynccontextmanager
import json
from io import BytesIO
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload
from pypdf import PdfReader

from app import drawing_estimator, models, pricing, schemas
from app.database import Base, SessionLocal, engine, get_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        pricing.seed_demo_prices(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Construction Cost Estimator", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
AUTO_FROM_DRAWING_PREFIX = "AUTO_FROM_DRAWING:"
AUTO_EXTRA_PREFIX = "AUTO_EXTRA:"
QUALITY_LEVELS = {"economy", "standard", "premium"}
BASE_RATE_NGN_PER_SQM = {
    "residential": {"economy": 280_000, "standard": 420_000, "premium": 650_000},
    "commercial": {"economy": 360_000, "standard": 550_000, "premium": 850_000},
}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/projects", response_model=schemas.ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: schemas.ProjectCreate, db: Session = Depends(get_db)) -> models.Project:
    project = models.Project(
        name=payload.name,
        project_type=payload.project_type,
        zip_code=payload.zip_code,
        status="draft",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def get_project_or_404(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def get_estimate_or_404(db: Session, estimate_version_id: int) -> models.EstimateVersion:
    db.expire_all()
    estimate_version = db.scalar(
        select(models.EstimateVersion)
        .execution_options(populate_existing=True)
        .options(selectinload(models.EstimateVersion.line_items))
        .where(models.EstimateVersion.id == estimate_version_id)
    )
    if not estimate_version:
        raise HTTPException(status_code=404, detail="Estimate version not found")
    return estimate_version


def extract_drawing_text(file_name: str, file_bytes: bytes) -> str:
    lower_name = file_name.lower()
    if lower_name.endswith(".pdf"):
        reader = PdfReader(BytesIO(file_bytes))
        pages: list[str] = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        return "\n".join(pages).strip()

    if lower_name.endswith(".txt"):
        return file_bytes.decode("utf-8", errors="ignore")

    # Fallback for unsupported binary files: treat as UTF-8 text best-effort.
    return file_bytes.decode("utf-8", errors="ignore")


def parse_known_prices(known_prices_json: str | None) -> dict[str, float]:
    known_prices: dict[str, float] = {}
    if not known_prices_json:
        return known_prices

    try:
        raw = json.loads(known_prices_json)
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="known_prices_json must be valid JSON") from err

    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="known_prices_json must be a JSON object")

    for key, value in raw.items():
        try:
            numeric_value = float(value)
        except (TypeError, ValueError) as err:
            raise HTTPException(status_code=400, detail=f"Invalid price for material '{key}'") from err
        if numeric_value <= 0:
            raise HTTPException(status_code=400, detail=f"Known price must be positive for '{key}'")
        known_prices[str(key)] = numeric_value

    return known_prices


def parse_additional_materials(additional_materials_json: str | None) -> list[dict[str, Any]]:
    if not additional_materials_json:
        return []

    try:
        raw = json.loads(additional_materials_json)
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="additional_materials_json must be valid JSON") from err

    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="additional_materials_json must be a JSON array")

    items: list[dict[str, Any]] = []
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise HTTPException(status_code=400, detail=f"additional_materials_json[{index}] must be an object")

        item_name = str(row.get("item_name", "")).strip()
        if not item_name:
            raise HTTPException(status_code=400, detail=f"additional_materials_json[{index}] missing item_name")

        try:
            quantity = float(row.get("quantity", 0))
        except (TypeError, ValueError) as err:
            raise HTTPException(status_code=400, detail=f"Invalid quantity for '{item_name}'") from err
        if quantity <= 0:
            raise HTTPException(status_code=400, detail=f"Quantity must be positive for '{item_name}'")

        try:
            waste = float(row.get("waste_factor_pct", 0))
        except (TypeError, ValueError) as err:
            raise HTTPException(status_code=400, detail=f"Invalid waste_factor_pct for '{item_name}'") from err
        if waste < 0:
            raise HTTPException(status_code=400, detail=f"waste_factor_pct cannot be negative for '{item_name}'")

        known_unit_price = None
        if row.get("known_unit_price") is not None and str(row.get("known_unit_price")).strip() != "":
            try:
                known_unit_price = float(row.get("known_unit_price"))
            except (TypeError, ValueError) as err:
                raise HTTPException(status_code=400, detail=f"Invalid known_unit_price for '{item_name}'") from err
            if known_unit_price <= 0:
                raise HTTPException(status_code=400, detail=f"known_unit_price must be positive for '{item_name}'")

        material_key = row.get("material_key")
        if material_key:
            material_key = drawing_estimator.normalize_material_key(str(material_key))
        else:
            material_key = drawing_estimator.normalize_material_key(item_name)

        items.append(
            {
                "material_key": material_key,
                "item_name": item_name,
                "csi_code": str(row.get("csi_code", "")).strip() or "CUSTOM-00",
                "unit": str(row.get("unit", "")).strip() or "ea",
                "quantity": round(quantity, 2),
                "waste_factor_pct": round(waste, 2),
                "known_unit_price": known_unit_price,
                "is_user_added": True,
            }
        )

    return items


def parse_assumption_overrides(assumptions_json: str | None) -> tuple[dict[str, Any], str]:
    if not assumptions_json:
        return {}, "standard"
    try:
        raw = json.loads(assumptions_json)
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="assumptions_json must be valid JSON") from err
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="assumptions_json must be a JSON object")

    overrides: dict[str, Any] = {}
    for field in ("floor_area_sqm", "floors", "bedrooms", "bathrooms", "roof_factor"):
        if field in raw and raw[field] is not None and str(raw[field]).strip() != "":
            try:
                overrides[field] = float(raw[field]) if field != "floors" and field not in {"bedrooms", "bathrooms"} else int(float(raw[field]))
            except (TypeError, ValueError) as err:
                raise HTTPException(status_code=400, detail=f"Invalid value for {field}") from err

    quality_level = str(raw.get("quality_level", "standard")).strip().lower() or "standard"
    if quality_level not in QUALITY_LEVELS:
        raise HTTPException(status_code=400, detail="quality_level must be one of: economy, standard, premium")
    return overrides, quality_level


def benchmark_totals_ngn(project_type: str, zip_code: str, assumptions: drawing_estimator.Assumptions, quality_level: str) -> dict[str, float]:
    category = "commercial" if "commercial" in project_type.lower() else "residential"
    base_rate = BASE_RATE_NGN_PER_SQM[category][quality_level]
    region_code = pricing.region_from_zip(zip_code)
    region_factor = {
        "NG-LAG": 1.08,
        "NG-ABJ": 1.12,
        "NG-RIV": 1.10,
        "NG-KAN": 0.94,
    }.get(region_code, 1.0)
    gross_area = assumptions.floor_area_sqm * assumptions.floors
    expected = gross_area * base_rate * region_factor
    return {
        "total_low": round(expected * 0.85, 2),
        "total_expected": round(expected, 2),
        "total_high": round(expected * 1.2, 2),
    }


@app.post(
    "/projects/{project_id}/takeoff-items",
    response_model=schemas.TakeoffItemRead,
    status_code=status.HTTP_201_CREATED,
)
def create_takeoff_item(
    project_id: int,
    payload: schemas.TakeoffItemCreate,
    db: Session = Depends(get_db),
) -> models.TakeoffItem:
    get_project_or_404(db, project_id)
    takeoff_item = models.TakeoffItem(
        project_id=project_id,
        csi_code=payload.csi_code,
        item_name=payload.item_name,
        quantity=payload.quantity,
        unit=payload.unit,
        waste_factor_pct=payload.waste_factor_pct,
        notes=payload.notes,
    )
    db.add(takeoff_item)
    db.commit()
    db.refresh(takeoff_item)
    return takeoff_item


@app.post(
    "/projects/{project_id}/drawings/auto-estimate",
    response_model=schemas.DrawingAutoEstimateResponse,
)
async def auto_estimate_from_drawing(
    project_id: int,
    drawing: UploadFile = File(...),
    known_prices_json: str | None = Form(default=None),
    additional_materials_json: str | None = Form(default=None),
    assumptions_json: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> schemas.DrawingAutoEstimateResponse:
    project = get_project_or_404(db, project_id)
    known_prices = parse_known_prices(known_prices_json)
    additional_materials = parse_additional_materials(additional_materials_json)
    assumption_overrides, quality_level = parse_assumption_overrides(assumptions_json)

    content = await drawing.read()
    if not content:
        raise HTTPException(status_code=400, detail="Drawing file is empty")

    try:
        drawing_text = extract_drawing_text(drawing.filename or "drawing", content)
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Unable to read drawing: {err}") from err

    assumptions = drawing_estimator.infer_assumptions(drawing_text)
    assumptions = drawing_estimator.apply_assumption_overrides(assumptions, assumption_overrides)
    materials = drawing_estimator.generate_material_requirements(assumptions)
    materials.extend(additional_materials)
    benchmark = benchmark_totals_ngn(project.project_type, project.zip_code, assumptions, quality_level)

    db.execute(
        delete(models.TakeoffItem).where(
            models.TakeoffItem.project_id == project.id,
            models.TakeoffItem.notes.like(f"{AUTO_FROM_DRAWING_PREFIX}%"),
        )
    )
    db.execute(
        delete(models.TakeoffItem).where(
            models.TakeoffItem.project_id == project.id,
            models.TakeoffItem.notes.like(f"{AUTO_EXTRA_PREFIX}%"),
        )
    )
    db.flush()

    total_low = 0.0
    total_expected = 0.0
    total_high = 0.0
    output_rows: list[schemas.AutoEstimatedMaterialRead] = []
    missing_prices: list[str] = []
    benchmark_adjustment_applied = False

    for row in materials:
        note_prefix = AUTO_EXTRA_PREFIX if row.get("is_user_added") else AUTO_FROM_DRAWING_PREFIX
        db.add(
            models.TakeoffItem(
                project_id=project.id,
                csi_code=row["csi_code"],
                item_name=row["item_name"],
                quantity=row["quantity"],
                unit=row["unit"],
                waste_factor_pct=row["waste_factor_pct"],
                notes=f"{note_prefix}{row['material_key']}",
            )
        )

        known_price = row.get("known_unit_price")
        if known_price is None:
            known_price = known_prices.get(row["material_key"])
        price_range = pricing.estimate_unit_price_range(
            db=db,
            project=project,
            csi_code=row["csi_code"],
            known_unit_price=known_price,
        )

        waste_multiplier = 1 + (row["waste_factor_pct"] / 100)
        line_low = round(row["quantity"] * price_range["unit_price_low"] * waste_multiplier, 2)
        line_expected = round(row["quantity"] * price_range["unit_price_expected"] * waste_multiplier, 2)
        line_high = round(row["quantity"] * price_range["unit_price_high"] * waste_multiplier, 2)
        total_low += line_low
        total_expected += line_expected
        total_high += line_high

        needs_user_price = price_range["price_source"] != "user_provided"
        if needs_user_price:
            missing_prices.append(row["material_key"])

        output_rows.append(
            schemas.AutoEstimatedMaterialRead(
                material_key=row["material_key"],
                item_name=row["item_name"],
                csi_code=row["csi_code"],
                unit=row["unit"],
                quantity=row["quantity"],
                unit_price_low=price_range["unit_price_low"],
                unit_price_expected=price_range["unit_price_expected"],
                unit_price_high=price_range["unit_price_high"],
                line_total_low=line_low,
                line_total_expected=line_expected,
                line_total_high=line_high,
                price_source=price_range["price_source"],
                freshness_status=price_range["freshness_status"],
                confidence_score=price_range["confidence_score"],
                needs_user_price=needs_user_price,
            )
        )

    # Keep totals realistic for Nigeria by adding an explicit line item
    # when extracted drawing detail is sparse and bottom-up underestimates.
    if total_expected < (benchmark["total_expected"] * 0.75):
        low_gap = round(max(0.0, benchmark["total_low"] - total_low), 2)
        expected_gap = round(max(0.0, benchmark["total_expected"] - total_expected), 2)
        high_gap = round(max(0.0, benchmark["total_high"] - total_high), 2)
        if expected_gap > 0:
            output_rows.append(
                schemas.AutoEstimatedMaterialRead(
                    material_key="labor_preliminaries",
                    item_name="Labour, preliminaries, logistics & contractor overhead",
                    csi_code="01-00-00",
                    unit="lot",
                    quantity=1.0,
                    unit_price_low=low_gap,
                    unit_price_expected=expected_gap,
                    unit_price_high=high_gap,
                    line_total_low=low_gap,
                    line_total_expected=expected_gap,
                    line_total_high=high_gap,
                    price_source="benchmark_adjustment",
                    freshness_status="fresh",
                    confidence_score=88.0,
                    needs_user_price=False,
                )
            )
            total_low += low_gap
            total_expected += expected_gap
            total_high += high_gap
            benchmark_adjustment_applied = True

    db.commit()

    message = (
        "Auto-estimate generated from drawing. "
        "Add known unit prices for higher accuracy; missing items use current market ranges. "
        f"Custom materials added: {len(additional_materials)}. "
        f"Quality: {quality_level}."
    )
    if benchmark_adjustment_applied:
        message += " A benchmark adjustment line was added because drawing detail was insufficient for full quantity extraction."

    return schemas.DrawingAutoEstimateResponse(
        project_id=project.id,
        file_name=drawing.filename or "drawing",
        assumptions=schemas.DrawingAssumptionsRead(
            floor_area_sqm=assumptions.floor_area_sqm,
            floors=assumptions.floors,
            bedrooms=assumptions.bedrooms,
            bathrooms=assumptions.bathrooms,
            roof_factor=assumptions.roof_factor,
            quality_level=quality_level,
        ),
        materials=output_rows,
        totals=schemas.AutoEstimateTotalsRead(
            total_low=round(total_low, 2),
            total_expected=round(total_expected, 2),
            total_high=round(total_high, 2),
        ),
        benchmark_totals=schemas.AutoEstimateTotalsRead(
            total_low=benchmark["total_low"],
            total_expected=benchmark["total_expected"],
            total_high=benchmark["total_high"],
        ),
        missing_unit_price_items=missing_prices,
        benchmark_adjustment_applied=benchmark_adjustment_applied,
        message=message,
    )


@app.post(
    "/projects/{project_id}/supplier-quotes/import",
    response_model=list[schemas.SupplierQuoteRead],
    status_code=status.HTTP_201_CREATED,
)
def import_supplier_quotes(
    project_id: int,
    payload: list[schemas.SupplierQuoteCreate],
    db: Session = Depends(get_db),
) -> list[models.SupplierQuote]:
    get_project_or_404(db, project_id)
    if not payload:
        raise HTTPException(status_code=400, detail="At least one quote is required")

    quotes: list[models.SupplierQuote] = []
    for row in payload:
        quote = models.SupplierQuote(
            project_id=project_id,
            supplier_name=row.supplier_name,
            quote_ref=row.quote_ref,
            csi_code=row.csi_code,
            item_name=row.item_name,
            unit=row.unit,
            quoted_unit_cost=row.quoted_unit_cost,
            valid_until=row.valid_until,
        )
        quotes.append(quote)

    db.add_all(quotes)
    db.commit()
    for quote in quotes:
        db.refresh(quote)
    return quotes


@app.post(
    "/projects/{project_id}/estimate-versions",
    response_model=schemas.EstimateVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_estimate_version(
    project_id: int,
    payload: schemas.EstimateVersionCreate,
    db: Session = Depends(get_db),
) -> models.EstimateVersion:
    get_project_or_404(db, project_id)
    estimate = models.EstimateVersion(
        project_id=project_id,
        version_name=payload.version_name,
        is_locked=False,
    )
    db.add(estimate)
    db.commit()
    db.refresh(estimate)
    estimate = get_estimate_or_404(db, estimate.id)
    return estimate


@app.post("/estimate-versions/{estimate_version_id}/reprice", response_model=schemas.EstimateVersionRead)
def reprice_estimate(estimate_version_id: int, db: Session = Depends(get_db)) -> models.EstimateVersion:
    estimate = get_estimate_or_404(db, estimate_version_id)
    if estimate.is_locked:
        raise HTTPException(status_code=409, detail="Estimate version is locked")

    project = get_project_or_404(db, estimate.project_id)
    takeoff_items = db.scalars(
        select(models.TakeoffItem).where(models.TakeoffItem.project_id == project.id)
    ).all()
    if not takeoff_items:
        raise HTTPException(status_code=400, detail="No takeoff items found for project")

    db.execute(
        delete(models.EstimateLineItem).where(models.EstimateLineItem.estimate_version_id == estimate.id)
    )
    db.flush()

    line_items: list[models.EstimateLineItem] = []
    for takeoff_item in takeoff_items:
        try:
            priced = pricing.price_takeoff_item(db, project, takeoff_item)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

        line_items.append(
            models.EstimateLineItem(
                estimate_version_id=estimate.id,
                takeoff_item_id=takeoff_item.id,
                unit_cost_selected=priced["unit_cost_selected"],
                source_trace_json=priced["source_trace_json"],
                last_price_update_at=priced["last_price_update_at"],
                freshness_status=priced["freshness_status"],
                confidence_score=priced["confidence_score"],
                subtotal_cost=priced["subtotal_cost"],
            )
        )

    db.add_all(line_items)
    db.commit()
    return get_estimate_or_404(db, estimate.id)


@app.post("/estimate-versions/{estimate_version_id}/lock", response_model=schemas.EstimateVersionRead)
def lock_estimate(estimate_version_id: int, db: Session = Depends(get_db)) -> models.EstimateVersion:
    estimate = get_estimate_or_404(db, estimate_version_id)
    estimate.is_locked = True
    db.add(estimate)
    db.commit()
    return get_estimate_or_404(db, estimate.id)


@app.get("/estimate-versions/{estimate_version_id}", response_model=schemas.EstimateVersionRead)
def get_estimate(estimate_version_id: int, db: Session = Depends(get_db)) -> models.EstimateVersion:
    return get_estimate_or_404(db, estimate_version_id)
