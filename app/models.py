from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    project_type: Mapped[str] = mapped_column(String(80), nullable=False)
    zip_code: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    takeoff_items: Mapped[list[TakeoffItem]] = relationship(back_populates="project")
    estimate_versions: Mapped[list[EstimateVersion]] = relationship(back_populates="project")
    supplier_quotes: Mapped[list[SupplierQuote]] = relationship(back_populates="project")


class TakeoffItem(Base):
    __tablename__ = "takeoff_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    csi_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    waste_factor_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    project: Mapped[Project] = relationship(back_populates="takeoff_items")


class PriceSource(Base):
    __tablename__ = "price_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    reliability_score: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    price_observations: Mapped[list[PriceObservation]] = relationship(back_populates="source")


class PriceCatalogItem(Base):
    __tablename__ = "price_catalog_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    csi_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    default_unit: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="material")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    observations: Mapped[list[PriceObservation]] = relationship(back_populates="catalog_item")


class PriceObservation(Base):
    __tablename__ = "price_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    price_catalog_item_id: Mapped[int] = mapped_column(
        ForeignKey("price_catalog_items.id"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("price_sources.id"), nullable=False, index=True)
    region_code: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    unit_cost: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    meta_json: Mapped[dict | None] = mapped_column(JSON)

    source: Mapped[PriceSource] = relationship(back_populates="price_observations")
    catalog_item: Mapped[PriceCatalogItem] = relationship(back_populates="observations")


class RegionMultiplier(Base):
    __tablename__ = "region_multipliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    region_code: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    csi_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class LaborRate(Base):
    __tablename__ = "labor_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    region_code: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    trade_code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    union_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hourly_rate: Mapped[float] = mapped_column(Float, nullable=False)
    burden_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class EstimateVersion(Base):
    __tablename__ = "estimate_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    version_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    project: Mapped[Project] = relationship(back_populates="estimate_versions")
    line_items: Mapped[list[EstimateLineItem]] = relationship(
        back_populates="estimate_version",
        cascade="all, delete-orphan",
    )


class EstimateLineItem(Base):
    __tablename__ = "estimate_line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    estimate_version_id: Mapped[int] = mapped_column(
        ForeignKey("estimate_versions.id"),
        nullable=False,
        index=True,
    )
    takeoff_item_id: Mapped[int] = mapped_column(ForeignKey("takeoff_items.id"), nullable=False, index=True)
    unit_cost_selected: Mapped[float] = mapped_column(Float, nullable=False)
    source_trace_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    last_price_update_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    freshness_status: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    subtotal_cost: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    estimate_version: Mapped[EstimateVersion] = relationship(back_populates="line_items")


class SupplierQuote(Base):
    __tablename__ = "supplier_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    supplier_name: Mapped[str] = mapped_column(String(160), nullable=False)
    quote_ref: Mapped[str | None] = mapped_column(String(120))
    csi_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    quoted_unit_cost: Mapped[float] = mapped_column(Float, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    project: Mapped[Project] = relationship(back_populates="supplier_quotes")
