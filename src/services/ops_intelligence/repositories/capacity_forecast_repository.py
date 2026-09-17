"""PostgresCapacityForecastRepository -- backs CapacityForecastSink against ``capacity_forecasts`` (migration 0030)."""

from __future__ import annotations

import json
from typing import Any

from src.libs.repositories.base import BaseRepository
from src.services.ops_intelligence.models import ConfidenceLevel
from src.services.ops_intelligence.reasoning.capacity_planner import CapacityForecast

_TABLE = "capacity_forecasts"
_COLUMNS = ("forecast_id", "tenant_id", "resource", "horizon_days", "forecast_data", "headroom_pct", "confidence", "generated_at")


class PostgresCapacityForecastRepository(BaseRepository):
    """Real Postgres-backed ``CapacityForecastSink`` implementation."""

    def create(self, forecast: CapacityForecast) -> CapacityForecast:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                forecast_id, tenant_id, resource, horizon_days, forecast_data, headroom_pct, confidence, generated_at
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            """,
            (
                forecast.forecast_id,
                forecast.tenant_id,
                forecast.resource,
                forecast.horizon_days,
                json.dumps(forecast.forecast_data),
                forecast.headroom_pct,
                forecast.confidence.value,
                forecast.generated_at,
            ),
        )
        self._commit()
        return forecast

    def latest(self, resource: str, *, tenant_id: str | None = None) -> CapacityForecast | None:
        where = "resource = %s"
        params: list[Any] = [resource]
        if tenant_id is not None:
            where += " AND tenant_id = %s"
            params.append(tenant_id)
        else:
            where += " AND tenant_id IS NULL"
        cur = self._execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE {where} ORDER BY generated_at DESC LIMIT 1", params
        )
        row = cur.fetchone()
        return _row_to_forecast(row) if row is not None else None

    def history(self, resource: str, *, tenant_id: str | None = None, limit: int = 12) -> tuple[CapacityForecast, ...]:
        where = "resource = %s"
        params: list[Any] = [resource]
        if tenant_id is not None:
            where += " AND tenant_id = %s"
            params.append(tenant_id)
        else:
            where += " AND tenant_id IS NULL"
        cur = self._execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE {where} ORDER BY generated_at DESC LIMIT %s",
            (*params, limit),
        )
        return tuple(_row_to_forecast(row) for row in cur.fetchall())


def _row_to_forecast(row: tuple[Any, ...]) -> CapacityForecast:
    forecast_id, tenant_id, resource, horizon_days, forecast_data, headroom_pct, confidence, generated_at = row
    return CapacityForecast(
        forecast_id=str(forecast_id),
        tenant_id=str(tenant_id) if tenant_id is not None else None,
        resource=resource,
        horizon_days=horizon_days,
        forecast_data=json.loads(forecast_data) if isinstance(forecast_data, str) else forecast_data,
        headroom_pct=headroom_pct,
        confidence=ConfidenceLevel(confidence),
        generated_at=generated_at,
    )


__all__ = ["PostgresCapacityForecastRepository"]
