from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app import models, pricing, schemas
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
