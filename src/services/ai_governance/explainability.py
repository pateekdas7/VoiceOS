"""ExplainabilityEngine — human-readable rationale from a DecisionEnvelope
(V4 Ch3 §3.11 "Explainability").

Architecture: V4 Ch3 (AI Governance — explainability).
"""

from __future__ import annotations

from src.libs.contracts.decision import DecisionEnvelope


class ExplainabilityEngine:
    """Renders a DecisionEnvelope's decision chain and governance verdict as prose."""

    def explain(self, envelope: DecisionEnvelope) -> str:
        """Build a human-readable rationale for supervisor/audit review."""
        lines = [f"Call {envelope.call_id} (tenant {envelope.tenant_id}) — turn decision {envelope.envelope_id}:"]

        for record in envelope.decisions:
            reason = record.reason
            lines.append(f"  - [{reason.source_engine}] {reason.decision} (confidence {reason.confidence:.2f})")
            if reason.reasoning:
                lines.append(f"      reasoning: {reason.reasoning}")
            if reason.evidence:
                lines.append(f"      evidence: {', '.join(reason.evidence)}")

        verdict = envelope.governance_verdict
        lines.append(f"  AI Governance verdict: {verdict.status.value}")
        if verdict.violations:
            lines.append(f"    violations: {', '.join(verdict.violations)}")
        if verdict.explanation:
            lines.append(f"    explanation: {verdict.explanation}")

        return "\n".join(lines)
