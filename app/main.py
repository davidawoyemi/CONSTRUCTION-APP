from contextlib import asynccontextmanager
import json
from io import BytesIO

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
    db: Session = Depends(get_db),
) -> schemas.DrawingAutoEstimateResponse:
    project = get_project_or_404(db, project_id)

    known_prices: dict[str, float] = {}
    if known_prices_json:
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

    content = await drawing.read()
    if not content:
        raise HTTPException(status_code=400, detail="Drawing file is empty")

    try:
        drawing_text = extract_drawing_text(drawing.filename or "drawing", content)
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Unable to read drawing: {err}") from err

    assumptions = drawing_estimator.infer_assumptions(drawing_text)
    materials = drawing_estimator.generate_material_requirements(assumptions)

    db.execute(
        delete(models.TakeoffItem).where(
            models.TakeoffItem.project_id == project.id,
            models.TakeoffItem.notes.like("AUTO_FROM_DRAWING:%"),
        )
    )
    db.flush()

    total_low = 0.0
    total_expected = 0.0
    total_high = 0.0
    output_rows: list[schemas.AutoEstimatedMaterialRead] = []
    missing_prices: list[str] = []

    for row in materials:
        db.add(
            models.TakeoffItem(
                project_id=project.id,
                csi_code=row["csi_code"],
                item_name=row["item_name"],
                quantity=row["quantity"],
                unit=row["unit"],
                waste_factor_pct=row["waste_factor_pct"],
                notes=f"AUTO_FROM_DRAWING:{row['material_key']}",
            )
        )

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

    db.commit()

    message = (
        "Auto-estimate generated from drawing. "
        "Add known unit prices for higher accuracy; missing items use current market ranges."
    )

    return schemas.DrawingAutoEstimateResponse(
        project_id=project.id,
        file_name=drawing.filename or "drawing",
        assumptions=schemas.DrawingAssumptionsRead(
            floor_area_sqm=assumptions.floor_area_sqm,
            floors=assumptions.floors,
            bedrooms=assumptions.bedrooms,
            bathrooms=assumptions.bathrooms,
            roof_factor=assumptions.roof_factor,
        ),
        materials=output_rows,
        totals=schemas.AutoEstimateTotalsRead(
            total_low=round(total_low, 2),
            total_expected=round(total_expected, 2),
            total_high=round(total_high, 2),
        ),
        missing_unit_price_items=missing_prices,
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
