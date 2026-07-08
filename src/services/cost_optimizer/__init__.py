"""Cost Optimization Service (V7 Ch15, Sprint-027).

Deviation from Sprint-027.md's literal path: the spec names this
directory ``src/services/cost-optimizer/`` (hyphen), which is not a valid
Python package name -- named ``cost_optimizer`` (underscore) instead, the
same precedent as every prior sprint's dashed-path fix (``admin-portal``
-> ``admin_portal``, ``bi-platform`` -> ``bi_platform``, etc).
"""

from __future__ import annotations

from .gpu_efficiency import GPUEfficiencyAnalyzer
from .instance_mix import InstanceMixOptimizer
from .models import ConversationResourceUsage, CostRecommendation, CostReport, DateRange, InstanceMixRecommendation
from .service import CostOptimizer
from .tracker import ConversationCostTracker

__all__ = [
    "ConversationCostTracker",
    "ConversationResourceUsage",
    "CostOptimizer",
    "CostRecommendation",
    "CostReport",
    "DateRange",
    "GPUEfficiencyAnalyzer",
    "InstanceMixOptimizer",
    "InstanceMixRecommendation",
]
