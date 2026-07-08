"""Campaign Management — audience targeting, RBI-compliant scheduling, retry, A/B testing (V5 Ch6).

Architecture: V5 Ch6 (Campaign Management); V4 Ch2 (RBI calling hours).
"""

from __future__ import annotations

from .ab_testing import ABTestingFramework
from .audience import AudienceSelector
from .call_dispatcher import CallDispatcher
from .lifecycle import CampaignLifecycle, CampaignLifecycleError
from .retry_policy import RetryPolicyEngine
from .scheduler import DNDStatusPort, ScheduleEngine
from .service import CampaignService

__all__ = [
    "ABTestingFramework",
    "AudienceSelector",
    "CallDispatcher",
    "CampaignLifecycle",
    "CampaignLifecycleError",
    "CampaignService",
    "DNDStatusPort",
    "RetryPolicyEngine",
    "ScheduleEngine",
]
