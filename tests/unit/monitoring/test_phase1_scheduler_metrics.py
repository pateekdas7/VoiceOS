"""Phase 1 — C2/C3 self-monitoring metrics.

Verifies:
- plumbing.metrics exposes escalation scheduler gauges/counters
- gpu_fleet.metrics exposes poller gauges/counters
- helper functions update them correctly

Run:
    python3 -m pytest tests/unit/monitoring/test_phase1_scheduler_metrics.py --noconftest -v
"""
from __future__ import annotations

import time


def test_plumbing_scheduler_metrics_exist() -> None:
    from src.services.ops_intelligence.plumbing import metrics as m

    assert hasattr(m, "escalation_scheduler_last_run_gauge")
    assert hasattr(m, "escalation_scheduler_errors_counter")
    assert hasattr(m, "escalations_counter")


def test_record_scheduler_tick_sets_gauge() -> None:
    from src.services.ops_intelligence.plumbing.metrics import (
        escalation_scheduler_last_run_gauge,
        record_scheduler_tick,
    )

    ts = time.time()
    record_scheduler_tick(ts)
    assert escalation_scheduler_last_run_gauge._value.get() == ts


def test_record_scheduler_error_increments_counter() -> None:
    from src.services.ops_intelligence.plumbing.metrics import (
        escalation_scheduler_errors_counter,
        record_scheduler_error,
    )

    before = escalation_scheduler_errors_counter._value.get()
    record_scheduler_error()
    record_scheduler_error()
    assert escalation_scheduler_errors_counter._value.get() == before + 2


def test_record_escalation_labels_by_severity() -> None:
    from src.services.ops_intelligence.plumbing.metrics import (
        escalations_counter,
        record_escalation,
    )

    record_escalation("critical")
    record_escalation("critical")
    record_escalation("warning")
    assert escalations_counter.labels(severity="critical")._value.get() >= 2
    assert escalations_counter.labels(severity="warning")._value.get() >= 1


def test_gpu_fleet_poller_metrics_exist() -> None:
    from monitoring.gpu_fleet import metrics as m

    assert hasattr(m, "POLLER_LAST_RUN")
    assert hasattr(m, "POLLER_ERRORS")


def test_gpu_fleet_record_poller_tick_sets_gauge() -> None:
    from monitoring.gpu_fleet.metrics import POLLER_LAST_RUN, record_poller_tick

    ts = time.time()
    record_poller_tick(ts)
    assert POLLER_LAST_RUN._value.get() == ts


def test_gpu_fleet_record_poller_error_increments_counter() -> None:
    from monitoring.gpu_fleet.metrics import POLLER_ERRORS, record_poller_error

    before = POLLER_ERRORS.labels(node_id="n1")._value.get()
    record_poller_error("n1")
    record_poller_error("n1")
    assert POLLER_ERRORS.labels(node_id="n1")._value.get() == before + 2
