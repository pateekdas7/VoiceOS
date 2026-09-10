"""LeadDistributionEngine — distributes valid leads across campaign pipelines.

Leads are sorted by score DESC (highest priority first) and assigned to
pipelines in round-robin order so each pipeline gets a balanced share.
All-or-nothing: if a campaign has no ACTIVE pipelines the leads remain
unassigned (pipeline_id = None) and can be redistributed once a pipeline
is activated.

Architecture: V5 Ch6 (Campaign Engine — Lead Distribution, ADR-005 §14).
"""

from __future__ import annotations

import re

from src.libs.contracts.models.pipeline import CampaignLead, LeadLanguage, LeadStatus
from src.libs.contracts.primitives import CampaignId, LeadId, PipelineId, TenantId

# ─────────────────────────────────────────────────────────────────────────────
# Phone normalization (India-centric E.164)
# ─────────────────────────────────────────────────────────────────────────────

_DIGITS_RE = re.compile(r"\D")


def normalize_phone(raw: str) -> str | None:
    """Normalize a raw phone string to E.164 (+91XXXXXXXXXX).

    Returns None if the number cannot be recognized as a valid Indian mobile.
    """
    digits = _DIGITS_RE.sub("", raw)
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return f"+{digits}"
    if len(digits) == 11 and digits.startswith("0") and digits[1] in "6789":
        return f"+91{digits[1:]}"
    if len(digits) == 13 and digits.startswith("091") and digits[3] in "6789":
        return f"+91{digits[3:]}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Language detection from metadata
# ─────────────────────────────────────────────────────────────────────────────

_STATE_LANGUAGE: dict[str, LeadLanguage] = {
    # key: lowercase stripped state name or code → language
    "maharashtra": LeadLanguage.MARATHI,
    "mh": LeadLanguage.MARATHI,
    "tamil nadu": LeadLanguage.TAMIL,
    "tn": LeadLanguage.TAMIL,
    "telangana": LeadLanguage.TELUGU,
    "andhra pradesh": LeadLanguage.TELUGU,
    "ap": LeadLanguage.TELUGU,
    "ts": LeadLanguage.TELUGU,
    "karnataka": LeadLanguage.KANNADA,
    "ka": LeadLanguage.KANNADA,
    "kerala": LeadLanguage.MALAYALAM,
    "kl": LeadLanguage.MALAYALAM,
    "gujarat": LeadLanguage.GUJARATI,
    "gj": LeadLanguage.GUJARATI,
    "west bengal": LeadLanguage.BENGALI,
    "wb": LeadLanguage.BENGALI,
    "odisha": LeadLanguage.ODIA,
    "or": LeadLanguage.ODIA,
    "punjab": LeadLanguage.PUNJABI,
    "pb": LeadLanguage.PUNJABI,
}

_EXPLICIT_LANG_MAP: dict[str, LeadLanguage] = {
    lang.value.lower(): lang for lang in LeadLanguage
}
_EXPLICIT_LANG_MAP.update({"hi": LeadLanguage.HINDI, "en": LeadLanguage.ENGLISH})


def detect_language(metadata: dict[str, str]) -> LeadLanguage:
    """Detect lead language from metadata fields (language > state fallback)."""
    explicit = metadata.get("language", "").lower().strip()
    if explicit and explicit in _EXPLICIT_LANG_MAP:
        return _EXPLICIT_LANG_MAP[explicit]
    state = metadata.get("state", "").lower().strip()
    if state in _STATE_LANGUAGE:
        return _STATE_LANGUAGE[state]
    return LeadLanguage.HINDI


# ─────────────────────────────────────────────────────────────────────────────
# Lead scoring
# ─────────────────────────────────────────────────────────────────────────────

def score_lead(metadata: dict[str, str], *, has_name: bool, has_email: bool) -> int:
    """Compute a 0–100 priority score for a lead.

    Higher score = more urgent / higher value = placed earlier in pipeline.

    Component breakdown (max 100):
      DPD tier     0–40  (older delinquency = higher urgency)
      Outstanding  0–20  (higher balance = higher value)
      Name         0–10  (data completeness signal)
      Email        0–5   (data completeness signal)
      Phone valid  15    (always awarded when scoring — invalid phones are
                         rejected before reaching this function)
    """
    dpd_str = metadata.get("dpd", "0")
    try:
        dpd = int(float(dpd_str))
    except (ValueError, TypeError):
        dpd = 0

    outstanding_str = metadata.get("outstanding", "0")
    try:
        outstanding = int(float(outstanding_str))
    except (ValueError, TypeError):
        outstanding = 0

    if dpd >= 90:
        dpd_score = 40
    elif dpd >= 60:
        dpd_score = 30
    elif dpd >= 31:
        dpd_score = 20
    elif dpd >= 1:
        dpd_score = 10
    else:
        dpd_score = 0

    if outstanding >= 100_000:
        outstanding_score = 20
    elif outstanding >= 50_000:
        outstanding_score = 15
    elif outstanding >= 10_000:
        outstanding_score = 10
    elif outstanding >= 1:
        outstanding_score = 5
    else:
        outstanding_score = 0

    name_score = 10 if has_name else 0
    email_score = 5 if has_email else 0
    phone_score = 15

    return min(100, dpd_score + outstanding_score + name_score + email_score + phone_score)


# ─────────────────────────────────────────────────────────────────────────────
# Distribution engine
# ─────────────────────────────────────────────────────────────────────────────

class LeadDistributionEngine:
    """Assigns leads to pipelines using score-ordered round-robin.

    Round-robin is deterministic given a fixed pipeline list: lead[0] → pipeline[0],
    lead[1] → pipeline[1], …, lead[N] → pipeline[N % len(pipelines)].
    This guarantees each pipeline receives equal load and the highest-scoring
    leads are spread across all pipelines rather than front-loading one.
    """

    def distribute(
        self,
        leads: list[CampaignLead],
        pipeline_ids: list[PipelineId],
    ) -> list[CampaignLead]:
        """Assign pipeline_ids to leads in score-descending round-robin order.

        Leads that are duplicates, blacklisted, or have a rejection_reason are
        skipped (they keep pipeline_id=None). Returns a new list of CampaignLead
        with pipeline_id and status updated (models are frozen — returns copies).
        """
        if not pipeline_ids:
            return leads

        eligible = [
            (i, lead)
            for i, lead in enumerate(leads)
            if not lead.is_duplicate and not lead.is_blacklisted and lead.rejection_reason is None
        ]
        # Sort eligible by score descending (highest priority first)
        eligible.sort(key=lambda pair: pair[1].score, reverse=True)

        result = list(leads)
        for slot, (original_idx, lead) in enumerate(eligible):
            assigned_pipeline = pipeline_ids[slot % len(pipeline_ids)]
            result[original_idx] = lead.model_copy(update={
                "pipeline_id": assigned_pipeline,
                "status": LeadStatus.ASSIGNED,
            })

        return result

    def redistribute(
        self,
        tenant_id: TenantId,
        campaign_id: CampaignId,
        unassigned_lead_ids: list[LeadId],
        pipeline_ids: list[PipelineId],
    ) -> list[tuple[LeadId, PipelineId]]:
        """Compute pipeline assignments for a set of previously unassigned lead IDs.

        Returns (lead_id, pipeline_id) pairs; caller is responsible for
        persisting via CampaignLeadRepository.assign_pipeline().
        """
        if not pipeline_ids or not unassigned_lead_ids:
            return []
        return [
            (lead_id, pipeline_ids[i % len(pipeline_ids)])
            for i, lead_id in enumerate(unassigned_lead_ids)
        ]
