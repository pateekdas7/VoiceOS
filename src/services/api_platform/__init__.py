"""API Platform — spec-first public REST API, API key auth, tiered rate limiting (V5 Ch16, Sprint-025)."""

from __future__ import annotations

from .api import create_public_api
from .openapi import get_openapi_schema
from .rate_limits import TIER_RPS_LIMITS, rps_for_tier

__all__ = ["TIER_RPS_LIMITS", "create_public_api", "get_openapi_schema", "rps_for_tier"]
