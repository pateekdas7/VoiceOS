"""Persistent data models for the Business Intelligence Platform (Sprint-024, V5 Ch21).

Architecture: V5 Ch21 (Business Intelligence Platform — BI warehouse,
forecasting, cross-tenant benchmarking, executive dashboards).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import TenantId


class BIDimTenant(BaseModel):
    """Dimension row mapping a real ``tenant_id`` to an opaque surrogate key.

    ``CrossTenantBenchmarking`` (Sprint-024) queries/joins only on
    ``tenant_surrogate_key`` — this is what makes "no tenant identifiers
    exposed" a schema-level guarantee, not just an application-layer
    promise. Persisted to ``bi_facts.dim_tenant`` (migration 0023).
    """

    model_config = ConfigDict(frozen=True)

    tenant_surrogate_key: str = Field(min_length=1)
    tenant_id: TenantId
    created_at: datetime


class BIFactDaily(BaseModel):
    """One tenant-day's BI fact row — the ``BIWarehouse.refresh()`` output
    (V5 Ch21), aggregating AnalyticsService + BillingService + MeteringService
    + ComplianceMonitoring.

    Persisted to ``bi_facts.fact_daily`` (migration 0023), keyed by the
    anonymized ``tenant_surrogate_key`` (see :class:`BIDimTenant`).
    """

    model_config = ConfigDict(frozen=True)

    fact_daily_id: str = Field(min_length=1)
    tenant_surrogate_key: str = Field(min_length=1)
    day: date
    revenue_minor: int = Field(default=0, ge=0)
    usage_call_minutes: int = Field(default=0, ge=0)
    usage_stt_tokens: int = Field(default=0, ge=0)
    usage_llm_tokens: int = Field(default=0, ge=0)
    usage_gpu_seconds: int = Field(default=0, ge=0)
    ptp_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    recovery_rate: float = Field(default=0.0, ge=0.0)
    compliance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    refreshed_at: datetime


__all__ = [
    "BIDimTenant",
    "BIFactDaily",
]
