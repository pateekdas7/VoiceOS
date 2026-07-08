# VoiceOS v2 — Documentation Suite

## Document 8 — Testing Catalog

**Type:** Canonical test reference (documentation layer over Volumes 1–7)
**Status:** Living reference · **Owner:** QA Engineering + per-domain owners
**Authority:** Volumes 1–7 are immutable + canonical. This catalog **consolidates** the testing already defined — V6 Ch9 (testing standards), V3 Ch20 (stress/chaos), V4 Ch21 (penetration testing), V2 Ch22 (quality), V1 Ch23 (latency), plus AI evaluation (DocSuite-10). It introduces no new test policy; the volumes are authoritative. Citations: `V<n> Ch<c>`.

> **Per-test columns:** Purpose · Inputs · Expected outputs · Acceptance criteria · Automation status · Frequency · Owner. **Principle (V6 Ch9):** tests are the executable encoding of the architecture; invariant tests cover every AR/RI rule; coverage floors are CI gates (≥90% core, ≥80% services, 100% of invariants have a test). Tests are deterministic; flaky tests are quarantined + fixed, never silently skipped.

---

## 1. Test pyramid (overview)

```
Unit → Integration → End-to-End → Load/Stress/Chaos        (V6 Ch9 / V3 Ch20)
   + AI evaluation (golden/replay/red-team)  (DocSuite-10 / V2 Ch22 / V4 Ch14/21)
   + Invariant tests (AR/RI rules)           (V6 Ch4 / V1 App.E)
   + Domain suites (pronunciation, compliance, latency, GPU, founder validation)
```

---

## 2. Unit tests  *(V6 Ch9 TEST-1)*
- **Purpose.** Verify pure logic in isolation (engines, validators, planners, builders, contracts) with no I/O.
- **Inputs.** Constructed `libs/contracts` objects (builders/factories); edge cases.
- **Expected outputs.** Correct behavior + contract conformance; deterministic.
- **Acceptance.** All pass; **≥90% line + meaningful branch coverage** on core logic (CI gate). Includes invariant unit tests (e.g., Prompt Builder deterministic = RI-7/AR-13; Output Validator rejects out-of-envelope = AR-6; effect idempotent = AR-15).
- **Automation.** Fully automated (CI). **Frequency.** Every commit/PR; suite < 2 min locally. **Owner.** Each component's team.

## 3. Integration tests  *(V6 Ch9 TEST-2)*
- **Purpose.** Verify a service against real dependencies (DB, Redis, event bus) via containers — catching SQL/serialization/transaction issues mocks hide.
- **Inputs.** Real containerized dependencies; representative data; explicit tenant-isolation cases.
- **Expected outputs.** Correct cross-component behavior; **tenant isolation holds** (AR-8).
- **Acceptance.** All pass; isolation tests prove no cross-tenant access; ≥80% service coverage (CI gate).
- **Automation.** Automated (CI). **Frequency.** Every PR. **Owner.** Service team.

## 4. End-to-end tests  *(V6 Ch9 TEST-3)*
- **Purpose.** Verify the full call path (media→STT→engine→LLM→validate→TTS→playback) on a prod-parity stack.
- **Inputs.** Audio-injection harness (V3 Ch20) feeding synthetic calls; representative scenarios.
- **Expected outputs.** Correct conversation behavior **and** latency budget respected (first-audio p95 ≤ 1.5 s, V1 Ch23).
- **Acceptance.** Scenarios pass; budget met; no dropped calls.
- **Automation.** Automated (CI on ephemeral stack). **Frequency.** Every PR + pre-release. **Owner.** QA + Voice Runtime.

## 5. Regression tests  *(V6 Ch9 TEST-6)*
- **Purpose.** Prevent recurrence — every fixed bug gets a test that failed before the fix, passes after.
- **Inputs.** The bug's reproduction; for AI bugs, a golden/replay case.
- **Expected outputs.** Test fails on the old code, passes on the fix.
- **Acceptance.** Present for every closed bug (PLAY-2); suite only grows.
- **Automation.** Automated. **Frequency.** Every PR (full suite in CI). **Owner.** Fixing engineer.

## 6. Load testing  *(V3 Ch20 / V6 TEST-4)*
- **Purpose.** Verify behavior + SLOs under expected + peak concurrent-call load.
- **Inputs.** Synthetic concurrent calls at target + peak concurrency (audio-injection harness).
- **Expected outputs.** SLOs hold; graceful behavior at limits.
- **Acceptance.** **first-audio p95 ≤ 1.5 s; 0 OOM; 0 recoverable-call drops; 0 duplicate effects; no leaks under soak.**
- **Automation.** Automated. **Frequency.** Runtime-affecting changes + nightly. **Owner.** Platform/SRE.

## 7. Stress testing  *(V3 Ch20)*
- **Purpose.** Find breaking points beyond peak — verify graceful degradation + shedding (V3 Ch14), not collapse.
- **Inputs.** Load beyond capacity; resource exhaustion.
- **Expected outputs.** Graceful shed of lowest-priority; hot path protected; recovery on relief.
- **Acceptance.** No catastrophic failure; **0 OOM** (RI-8); degradation matches the ladder (V1 Ch24).
- **Automation.** Automated. **Frequency.** Pre-release + periodic. **Owner.** Platform/SRE.

## 8. Chaos testing  *(V3 Ch20 / V7 Ch20)*
- **Purpose.** Verify resilience by injecting failures (component/AZ/region/dependency kills, latency, resource exhaustion).
- **Inputs.** Fault injection in production-like (and carefully, production) environments.
- **Expected outputs.** Graceful degradation + deterministic recovery (V3 Ch7); SLO impact bounded; RTO/RPO met.
- **Acceptance.** Survives injected failures; **0 committed-effect loss**; gaps found → fixed (BCP-1).
- **Automation.** Automated game-days + surprise drills. **Frequency.** Regular (V7 Ch20). **Owner.** Platform/SRE + Continuity.

## 9. Founder validation  *(domain acceptance)*
- **Purpose.** Human expert (founder/domain lead) judges real-world conversation quality + collections appropriateness before pilot/release — the qualitative go/no-go complementing automated eval.
- **Inputs.** Curated + live-sampled conversations; the founder-validation rubric (DocSuite-10/12).
- **Expected outputs.** A founder validation report (template DocSuite-12) with pass/fail + notes.
- **Acceptance.** Meets the qualitative bar (tone, compliance, negotiation sense, empathy); blocking for pilot.
- **Automation.** Manual (structured rubric). **Frequency.** Pre-pilot + major AI changes. **Owner.** AI Engineering + founder/domain lead.

## 10. Conversation replay  *(V6 Ch9 TEST-5 / DocSuite-10)*
- **Purpose.** Detect behavioral regressions deterministically by replaying recorded calls (using recorded decisions, V3 Ch7) against new prompts/models.
- **Inputs.** Replay set of recorded conversations + their `DecisionEnvelope` lineage.
- **Expected outputs.** Behavior matches expected (or intended improvement); no regression.
- **Acceptance.** No behavioral regression vs baseline; new failures added to the set.
- **Automation.** Automated (AI-eval gate, V6 CICD-5). **Frequency.** Every AI/prompt/model change. **Owner.** AI Engineering.

## 11. Pronunciation testing  *(V1 Ch15–20 / DocSuite-07)*
- **Purpose.** Verify TTS pronounces terminology, amounts, dates, names, and brand names correctly per the Voice Style Guide.
- **Inputs.** Pronunciation golden set (terms, amounts, dates, Hindi/Hinglish/English, brand overrides).
- **Expected outputs.** Correct, consistent pronunciation; normalization applied (no raw tokens).
- **Acceptance.** **Pronunciation accuracy targets met; forbidden pronunciations = 0** (DocSuite-07 §8); MOS bar met.
- **Automation.** Automated (where measurable) + human spot-check. **Frequency.** Every voice/TTS change. **Owner.** Voice Runtime + AI Engineering.

## 12. Compliance testing  *(V4 Ch2/14 / DocSuite-06)*
- **Purpose.** Verify regulatory behavior — verification-before-disclosure, mandatory disclosures, calling windows, no harassment, envelope adherence.
- **Inputs.** Compliance scenario suite (RBI/DPDP cases); red-team prompts (V4 Ch21).
- **Expected outputs.** Compliant behavior in all cases; violations blocked.
- **Acceptance.** **0 pre-verification disclosures; 100% mandatory-disclosure completeness; 0 out-of-envelope offers; 0 unauthorized effects** (Law of Authority red-team).
- **Automation.** Automated (compliance + red-team suites, CI gate). **Frequency.** Every AI/policy change + release. **Owner.** Security & Governance + AI Engineering.

## 13. Latency testing  *(V1 Ch23 / V3 Ch19)*
- **Purpose.** Verify the latency budget per stage + end-to-end.
- **Inputs.** Traced calls under load; per-budget-line measurement (V3 Ch17 / DocSuite via traces).
- **Expected outputs.** Each budget line + end-to-end within target.
- **Acceptance.** **first-audio p95 ≤ 1.5 s**; no budget-line regression vs baseline.
- **Automation.** Automated (perf gate, V3 Ch19 / V6 Ch12). **Frequency.** Every runtime/AI change + continuous. **Owner.** Voice Runtime + Platform/SRE.

## 14. GPU testing  *(V1 Ch7 / V7 Ch6)*
- **Purpose.** Verify GPU admission control + OOM-by-construction + failover under saturation.
- **Inputs.** GPU-saturation load; injected GPU faults (V3 Ch20).
- **Expected outputs.** Admission shed (not OOM); warm-before-admit; graceful failover (GPU-2).
- **Acceptance.** **0 OOM under any load** (RI-8); VRAM ledger consistent; failover drops no calls.
- **Automation.** Automated (chaos/stress). **Frequency.** Pre-release + periodic. **Owner.** Voice Runtime / Platform.

## 15. Security testing / penetration  *(V4 Ch21)*
- **Purpose.** Find vulnerabilities — authz/isolation bypass, injection, API abuse, prompt injection/jailbreak.
- **Inputs.** Pen-test scenarios; red-team prompt suite; SAST/DAST.
- **Expected outputs.** No exploitable vulnerabilities; tenant isolation + Law of Authority hold.
- **Acceptance.** **0 isolation breaches; 0 unauthorized effects; critical findings remediated on SLA** (V7 Ch18).
- **Automation.** Automated scans (CI) + periodic manual pen-test. **Frequency.** Continuous scan + periodic pen-test. **Owner.** Security & Governance.

---

## 16. Invariant test matrix (AR/RI coverage)

> Every invariant has at least one explicit test (V6 Ch9 TEST-7, 100% gate). Representative mapping:

| Invariant | Test type | Asserts |
|---|---|---|
| RI-1 (no media-thread block) | unit/lint | no sync I/O on async path (AR-9) |
| RI-2 (single-writer) | review/unit | one writer per state (AR-10) |
| RI-3 (bounded buffers) | unit/load | bounded; eviction at cap (AR-11) |
| RI-4 (commit-before-act) | integration | durable commit precedes effect (AR-12) |
| RI-5 (Law of Authority) | compliance/red-team | model cannot originate authoritative value (AR-3) |
| RI-6 (ordering/flush) | e2e | output coherence; no dead air |
| RI-7 (deterministic prompt) | unit | same plan+version ⇒ identical prompt (AR-13) |
| RI-8 (OOM-by-construction) | GPU/chaos | 0 OOM under saturation (AR-14) |
| AR-8 (tenant isolation) | integration/pen-test | no cross-tenant access |
| AR-15 (idempotency) | integration | duplicate effect = 0 |

---

## 17. Coverage gates (CI) & hygiene

| Gate | Target | Source |
|---|---|---|
| Core logic coverage | ≥ 90% | V6 TEST-7 |
| Service coverage | ≥ 80% | V6 TEST-7 |
| Invariants with a test | 100% | V6 TEST-7 |
| Load acceptance | p95 ≤ 1.5 s, 0 OOM/drops/dup, no leaks | V3 Ch20 |
| AI eval (on AI change) | no quality regression; 0 unauthorized effects | V6 CICD-5 |

**Hygiene:** deterministic tests (seeded, no real time/network); flaky → quarantine + fix; tests are production code (typed, reviewed, owned).

## 18. Frequency summary

| Cadence | Tests |
|---|---|
| Every commit/PR | unit, integration, e2e, regression, coverage gates, AI eval (if AI touched), security scan |
| Nightly | load; broader e2e |
| Pre-release | stress, GPU, founder validation, full compliance, pen-test (periodic) |
| Regular game-days | chaos; DR drills (V7 Ch14/20) |
| Continuous | latency (perf gate), security scanning |

---

## Version history & change log

| Version | Date | Change | Owner |
|---|---|---|---|
| 1.0 | (initial) | Testing catalog consolidated from V6 Ch9 / V3 Ch20 / V4 Ch21 / V2 Ch22 | QA / Documentation |

**Change-log policy:** any new test type or acceptance gate in a volume MUST be recorded here. The suite consistency audit (DocSuite-12) verifies testing coverage is documented.

*End of Document 8 — Testing Catalog.*
