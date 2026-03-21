from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str
    project_type: str
    zip_code: str


class ProjectRead(BaseModel):
    id: int
    name: str
    project_type: str
    zip_code: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class TakeoffItemCreate(BaseModel):
    csi_code: str
    item_name: str
    quantity: float = Field(gt=0)
    unit: str
    waste_factor_pct: float = Field(default=0.0, ge=0)
    notes: str | None = None


class TakeoffItemRead(BaseModel):
    id: int
    project_id: int
    csi_code: str
    item_name: str
    quantity: float
    unit: str
    waste_factor_pct: float
    notes: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SupplierQuoteCreate(BaseModel):
    supplier_name: str
    quote_ref: str | None = None
    csi_code: str
    item_name: str
    unit: str
    quoted_unit_cost: float = Field(gt=0)
    valid_until: datetime | None = None


class SupplierQuoteRead(BaseModel):
    id: int
    project_id: int
    supplier_name: str
    quote_ref: str | None = None
    csi_code: str
    item_name: str
    unit: str
    quoted_unit_cost: float
    valid_until: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class EstimateVersionCreate(BaseModel):
    version_name: str


class EstimateLineItemRead(BaseModel):
    id: int
    takeoff_item_id: int
    unit_cost_selected: float
    source_trace_json: dict
    last_price_update_at: datetime
    freshness_status: str
    confidence_score: float
    subtotal_cost: float

    model_config = ConfigDict(from_attributes=True)


class EstimateVersionRead(BaseModel):
    id: int
    project_id: int
    version_name: str
    is_locked: bool
    created_at: datetime
    line_items: list[EstimateLineItemRead]

    model_config = ConfigDict(from_attributes=True)
