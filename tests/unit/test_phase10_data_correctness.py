"""Phase 10 Verification Tests — Data Correctness and CRM

10a. CRM match during import — already wired in bff.js upload handler
10b. require_crm_match_before_dial flag — migration + bff.js create/update/queue gating
10c. amount_collected_minor fix — FulfilledPTPRepositoryPort + real query path
10d. avg_dpd fix — LoanAccountRepositoryPort + real query path

Run with: python3 tests/unit/test_phase10_data_correctness.py
Strategy: pure source inspection — reads .py/.js as text, no eval, no src imports.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

passed = 0
failed = 0
results: list[tuple[str, str, str]] = []


def test(name: str) -> "object":
    def _dec(fn: "object") -> "object":
        global passed, failed
        try:
            fn()  # type: ignore[operator]
            results.append((name, "PASS", ""))
            passed += 1
        except AssertionError as e:
            results.append((name, "FAIL", str(e)))
            failed += 1
        except Exception as e:
            results.append((name, "FAIL", f"{type(e).__name__}: {e}"))
            failed += 1
        return fn
    return _dec


BFF_SRC        = (ROOT / "bff.js").read_text()
MIGRATION_SRC  = (ROOT / "scripts/db/migrations/alembic/versions/0038_require_crm_match.py").read_text()
CAMP_ANA_SRC   = (ROOT / "src/services/analytics/campaign_analytics.py").read_text()
CALL_ANA_SRC   = (ROOT / "src/services/analytics/call_analytics.py").read_text()

# ---------------------------------------------------------------------------
# 10a: CRM match during import (already in bff.js)
# ---------------------------------------------------------------------------

@test("10a: bff.js has crmMatchPhones helper")
def _(): assert "crmMatchPhones" in BFF_SRC

@test("10a: bff.js upload handler counts crm_matched")
def _(): assert "crm_matched" in BFF_SRC

@test("10a: bff.js upload handler counts crm_unmatched")
def _(): assert "crm_unmatched" in BFF_SRC

@test("10a: bff.js upload response includes crm_matched/unmatched")
def _():
    idx = BFF_SRC.index("lead.upload_complete")
    region = BFF_SRC[idx:idx + 400]
    assert "crm_matched" in region and "crm_unmatched" in region

@test("10a: bff.js stores crm_match_status in lead metadata")
def _(): assert "crm_match_status" in BFF_SRC

# ---------------------------------------------------------------------------
# 10b: require_crm_match_before_dial flag
# ---------------------------------------------------------------------------

@test("10b: migration 0038 exists")
def _(): assert (ROOT / "scripts/db/migrations/alembic/versions/0038_require_crm_match.py").exists()

@test("10b: migration adds require_crm_match_before_dial column to campaigns")
def _(): assert "require_crm_match_before_dial" in MIGRATION_SRC

@test("10b: migration sets DEFAULT FALSE")
def _(): assert "DEFAULT FALSE" in MIGRATION_SRC

@test("10b: migration revision is 0038 down_revision 0037")
def _():
    assert 'revision: str = "0038"' in MIGRATION_SRC
    assert 'down_revision: str | None = "0037"' in MIGRATION_SRC

@test("10b: bff.js campaign create accepts require_crm_match_before_dial")
def _():
    # Find INSERT INTO campaigns and check the nearby text for the flag
    idx = BFF_SRC.index("INSERT INTO campaigns")
    region = BFF_SRC[idx:idx + 350]
    assert "require_crm_match_before_dial" in region

@test("10b: bff.js campaign update accepts require_crm_match_before_dial")
def _():
    # The PUT /campaigns/:id UPDATE query — needs a wide window to reach $13
    idx = BFF_SRC.index("UPDATE campaigns SET")
    region = BFF_SRC[idx:idx + 900]
    assert "require_crm_match_before_dial" in region

@test("10b: bff.js processOneRow gates Redis push on !require_crm_match_before_dial")
def _():
    assert "campaign.require_crm_match_before_dial" in BFF_SRC

@test("10b: bff.js queues MATCHED leads after CRM check when flag set")
def _():
    assert "byStatus.MATCHED" in BFF_SRC

@test("10b: bff.js resume handler also queues MATCHED leads when flag set")
def _():
    count = BFF_SRC.count("byStatus.MATCHED")
    assert count >= 2, f"Expected byStatus.MATCHED in both upload and resume handlers, found {count}"

# ---------------------------------------------------------------------------
# 10c: Fix amount_collected_minor in campaign_analytics.py
# ---------------------------------------------------------------------------

@test("10c: FulfilledPTPRepositoryPort is defined")
def _(): assert "class FulfilledPTPRepositoryPort" in CAMP_ANA_SRC

@test("10c: FulfilledPTPRepositoryPort has sum_fulfilled_amount method")
def _(): assert "sum_fulfilled_amount" in CAMP_ANA_SRC

@test("10c: CampaignAnalytics accepts ptp_repository parameter")
def _(): assert "ptp_repository" in CAMP_ANA_SRC

@test("10c: amount_collected_minor delegates to ptp_repository when set")
def _():
    idx = CAMP_ANA_SRC.index("def amount_collected_minor")
    region = CAMP_ANA_SRC[idx:idx + 300]
    assert "self._ptp" in region

@test("10c: amount_collected_minor returns 0 when no ptp_repository")
def _():
    idx = CAMP_ANA_SRC.index("def amount_collected_minor")
    region = CAMP_ANA_SRC[idx:idx + 300]
    assert "return 0" in region

@test("10c: amount_collected_minor calls sum_fulfilled_amount")
def _():
    idx = CAMP_ANA_SRC.index("def amount_collected_minor")
    region = CAMP_ANA_SRC[idx:idx + 300]
    assert "sum_fulfilled_amount" in region

@test("10c: FulfilledPTPRepositoryPort exported in __all__")
def _(): assert "FulfilledPTPRepositoryPort" in CAMP_ANA_SRC.split("__all__", 1)[1]

# ---------------------------------------------------------------------------
# 10d: Fix avg_dpd in call_analytics.py
# ---------------------------------------------------------------------------

@test("10d: LoanAccountRepositoryPort is defined in call_analytics.py")
def _(): assert "class LoanAccountRepositoryPort" in CALL_ANA_SRC

@test("10d: LoanAccountRepositoryPort has avg_dpd method signature")
def _(): assert "def avg_dpd" in CALL_ANA_SRC

@test("10d: CallAnalytics accepts loan_repository parameter")
def _(): assert "loan_repository" in CALL_ANA_SRC

@test("10d: CallAnalytics.avg_dpd method exists in class body")
def _():
    # Check that avg_dpd appears in the CallAnalytics class body (after class definition)
    class_idx = CALL_ANA_SRC.index("class CallAnalytics")
    region = CALL_ANA_SRC[class_idx:]
    assert "def avg_dpd" in region

@test("10d: CallAnalytics.avg_dpd delegates to self._loans")
def _():
    class_idx = CALL_ANA_SRC.index("class CallAnalytics")
    region = CALL_ANA_SRC[class_idx:]
    avg_idx = region.index("def avg_dpd")
    method_region = region[avg_idx:avg_idx + 250]
    assert "self._loans" in method_region

@test("10d: CallAnalytics.avg_dpd returns 0.0 when no loan_repository")
def _():
    class_idx = CALL_ANA_SRC.index("class CallAnalytics")
    region = CALL_ANA_SRC[class_idx:]
    avg_idx = region.index("def avg_dpd")
    method_region = region[avg_idx:avg_idx + 250]
    assert "0.0" in method_region

@test("10d: LoanAccountRepositoryPort exported in __all__")
def _(): assert "LoanAccountRepositoryPort" in CALL_ANA_SRC.split("__all__", 1)[1]

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

print("\n── Phase 10 Data Correctness & CRM Tests ─────────────────────────────")
for name, status, detail in results:
    line = f"  [{status}] {name}"
    if detail:
        line += f"\n         {detail}"
    print(line)
print(f"\n  {passed} passed, {failed} failed\n")
sys.exit(1 if failed > 0 else 0)
