"""Unit tests for the default MetricCheckSpec catalog (ADR-006 Sec 3.4/9 Phase 5)."""

from __future__ import annotations

from src.services.ops_intelligence.default_checks import DEFAULT_METRIC_CHECK_SPECS


class TestDefaultMetricCheckSpecs:
    def test_at_least_one_spec_defined(self) -> None:
        assert len(DEFAULT_METRIC_CHECK_SPECS) > 0

    def test_every_spec_has_a_unique_name(self) -> None:
        names = [spec.name for spec in DEFAULT_METRIC_CHECK_SPECS]
        assert len(names) == len(set(names))

    def test_every_spec_has_non_empty_queries(self) -> None:
        for spec in DEFAULT_METRIC_CHECK_SPECS:
            assert spec.query
            assert spec.baseline_query
            assert spec.affected_component

    def test_every_spec_uses_a_valid_comparison(self) -> None:
        for spec in DEFAULT_METRIC_CHECK_SPECS:
            assert spec.comparison in ("higher_is_worse", "lower_is_worse")

    def test_covers_stt_llm_tts_and_infra(self) -> None:
        components = {spec.affected_component for spec in DEFAULT_METRIC_CHECK_SPECS}
        assert {"stt", "llm_runtime", "tts", "gpu_fleet"}.issubset(components)
