"""RateCard — versioned unit pricing for usage-based billing (V5 Ch9, Sprint-024).

Volume 5 Ch9 deliberately leaves concrete prices/limits unspecified (it names
the meter dimensions, not numbers) — this module is where Sprint-024 picks
one self-consistent rate card, exactly as Sprint-023 picked concrete
RBI/HITL-SLA numbers not sourced from the architecture (see CHANGELOG.md).
Tests assert against *this* rate card's numbers, not an external authority.

Money is always integer minor units (paise) — batch pricing (e.g. "per 1000
tokens") is expressed as ``price_per_unit_minor`` charged per ``unit_size``
quantity, with the total computed by floor division so no float ever enters
a monetary calculation (V6 DM-2).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.libs.contracts.models.billing import InvoiceLineItem, SubscriptionTier, UsageType

RATE_CARD_VERSION = "v1"

TRIAL_MAX_CALLS = 100
"""TRIAL tier: total call count over the trial's lifetime (V5 Ch9, Sprint-024.md)."""

TRIAL_MAX_DAYS = 30
"""TRIAL tier: subscription validity window in days (V5 Ch9, Sprint-024.md)."""


@dataclass(frozen=True)
class RateCardEntry:
    """One usage dimension's pricing: ``price_per_unit_minor`` charged per ``unit_size`` units."""

    unit_size: int
    price_per_unit_minor: int
    description: str

    def total_cost_minor(self, quantity: int) -> int:
        """Cost for ``quantity`` raw units, floor-divided to the nearest whole minor unit."""
        return (quantity * self.price_per_unit_minor) // self.unit_size


@dataclass(frozen=True)
class RateCard:
    """A versioned, immutable set of per-usage-type prices."""

    version: str
    currency: str
    entries: dict[UsageType, RateCardEntry]

    def line_item_for(self, usage_type: UsageType, quantity: int) -> InvoiceLineItem:
        entry = self.entries[usage_type]
        return InvoiceLineItem(
            usage_type=usage_type,
            description=entry.description,
            quantity=quantity,
            unit_cost_minor=entry.price_per_unit_minor // entry.unit_size,
            total_minor=entry.total_cost_minor(quantity),
        )

    def cost_minor(self, usage_type: UsageType, quantity: int) -> int:
        return self.entries[usage_type].total_cost_minor(quantity)


DEFAULT_RATE_CARD = RateCard(
    version=RATE_CARD_VERSION,
    currency="INR",
    entries={
        UsageType.CALL_MINUTE: RateCardEntry(1, 200, "Call minutes"),
        UsageType.STT_TOKEN: RateCardEntry(1000, 500, "STT tokens (per 1,000)"),
        UsageType.LLM_TOKEN: RateCardEntry(1000, 1000, "LLM tokens (per 1,000)"),
        UsageType.GPU_SECOND: RateCardEntry(100, 1000, "GPU-seconds (per 100)"),
        UsageType.STORAGE_MB: RateCardEntry(1024, 500, "Storage (per GB/month)"),
        UsageType.AI_TOKEN: RateCardEntry(1000, 1000, "AI tokens (per 1,000)"),
        UsageType.SMS_MESSAGE: RateCardEntry(1, 20, "SMS messages"),
        UsageType.API_CALL: RateCardEntry(1, 5, "API calls"),
    },
)

TIER_USAGE_LIMITS: dict[SubscriptionTier, dict[UsageType, int | None]] = {
    SubscriptionTier.TRIAL: {
        UsageType.CALL_MINUTE: 1_000,
        UsageType.STT_TOKEN: 100_000,
        UsageType.LLM_TOKEN: 100_000,
        UsageType.GPU_SECOND: 10_000,
        UsageType.STORAGE_MB: 1_024,
    },
    SubscriptionTier.STARTER: {
        UsageType.CALL_MINUTE: 5_000,
        UsageType.STT_TOKEN: 500_000,
        UsageType.LLM_TOKEN: 500_000,
        UsageType.GPU_SECOND: 50_000,
        UsageType.STORAGE_MB: 5_120,
    },
    SubscriptionTier.GROWTH: {
        UsageType.CALL_MINUTE: 50_000,
        UsageType.STT_TOKEN: 5_000_000,
        UsageType.LLM_TOKEN: 5_000_000,
        UsageType.GPU_SECOND: 500_000,
        UsageType.STORAGE_MB: 51_200,
    },
    SubscriptionTier.ENTERPRISE: {
        UsageType.CALL_MINUTE: None,
        UsageType.STT_TOKEN: None,
        UsageType.LLM_TOKEN: None,
        UsageType.GPU_SECOND: None,
        UsageType.STORAGE_MB: None,
    },
    SubscriptionTier.ENTERPRISE_PLUS: {
        UsageType.CALL_MINUTE: None,
        UsageType.STT_TOKEN: None,
        UsageType.LLM_TOKEN: None,
        UsageType.GPU_SECOND: None,
        UsageType.STORAGE_MB: None,
    },
}
"""Per-tier usage limits by ``UsageType`` (V5 Ch9 §9.2). ``None`` = unlimited
(ENTERPRISE/ENTERPRISE_PLUS are contract-negotiated — V5 Ch9's "dedicated
infra" tier has no platform-enforced usage ceiling)."""

BASE_FEE_MINOR: dict[SubscriptionTier, int] = {
    SubscriptionTier.TRIAL: 0,
    SubscriptionTier.STARTER: 999_00,
    SubscriptionTier.GROWTH: 0,
    SubscriptionTier.ENTERPRISE: 50_000_00,
    SubscriptionTier.ENTERPRISE_PLUS: 150_000_00,
}
"""Monthly base/platform fee by tier, in minor currency units. GROWTH is
pure usage-based per V5 Ch9 ("GROWTH: usage-based"); ENTERPRISE/ENTERPRISE_PLUS
carry a negotiated base fee representative of "contract, dedicated infra"."""


__all__ = [
    "BASE_FEE_MINOR",
    "DEFAULT_RATE_CARD",
    "RATE_CARD_VERSION",
    "TIER_USAGE_LIMITS",
    "TRIAL_MAX_CALLS",
    "TRIAL_MAX_DAYS",
    "RateCard",
    "RateCardEntry",
]
