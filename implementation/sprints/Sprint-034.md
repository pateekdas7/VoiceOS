# Sprint-034 — Conversation Learning Layer

**Epic:** E3-Extended — Conversation Intelligence (Offline Learning)
**Status:** ⬜ Pending
**Depends on:** Sprint-010, Sprint-012, Sprint-029
**Blocks:** Sprint-031
**Milestone:** Learning Layer Complete

---

## Objective

Implement the offline Conversation Learning Layer: a pipeline that mines production calls for poor-quality turns, generates weak supervision labels, tracks model drift over time, orchestrates fine-tuning runs, and safely promotes improved models after A/B validation. This sprint does not change the real-time call path — it is entirely offline and runs after Sprint-029 (Founder Validation), which provides the first labeled evaluation data.

---

## Architecture References

- Volume 2: Ch18 (Learning Layer — offline model improvement pipeline: failed-turn mining, weak-label generation, model drift tracking, offline training, model promotion via A/B test)
- Volume 6: Ch9 (Testing — model evaluation must pass same quality gates as human validation before promotion)
- DocSuite-10: AI Evaluation Handbook (evaluation rubric used for model promotion gate)

---

## Components to Implement

### `src/services/learning-layer/`

```
src/services/learning-layer/
├── __init__.py
├── mining/
│   ├── failed_turn_miner.py    (FailedTurnMiner: identifies low-quality turns from OutputEvaluationEngine scores)
│   └── selection_criteria.py  (MiningCriteria: configurable quality thresholds for mining)
├── labeling/
│   ├── weak_label_generator.py (WeakLabelGenerator: generates training labels using heuristics + human corrections)
│   └── label_schema.py        (LabelSchema: intent labels, entity labels, quality dimensions — matches Sprint-010 IntentEngine schema)
├── drift/
│   ├── drift_tracker.py        (ModelDriftTracker: monitors intent classification accuracy over time)
│   └── drift_alert.py          (DriftAlerter: alerts when accuracy drops below threshold)
├── training/
│   ├── pipeline.py             (OfflineTrainingPipeline: orchestrates data collection → fine-tuning → evaluation)
│   └── dataset.py              (TrainingDataset: constructs training dataset from mined + labeled turns)
└── promotion/
    ├── promoter.py             (ModelPromoter: runs A/B test comparing new vs. current model)
    └── gate.py                 (PromotionGate: same quality criteria as Sprint-029 Founder Validation rubric)
```

**FailedTurnMiner:**
- Queries MongoDB `call_quality` collection (populated by ConversationQualityScorer, Sprint-012)
- Mining criteria: `factual_accuracy_score < 0.8 OR policy_compliance_score < 0.9 OR coherence_score < 0.7`
- `mine(date_range, criteria) -> list[FailedTurn]` — returns turns with transcript, engine outputs, and quality scores
- Output: `FailedTurn(call_id, turn_index, transcript, engine_outputs, quality_scores, failure_modes)`

**WeakLabelGenerator:**
- Takes FailedTurn list and generates intent/entity labels using:
  1. Heuristic rules (high-confidence cases)
  2. Human correction interface (for ambiguous cases flagged for review)
- `generate_labels(turns: list[FailedTurn]) -> list[LabeledTurn]` — returns turns with weak supervision labels
- Labels persisted in Postgres `training_labels` table (append-only, versioned by label_set_id)

**ModelDriftTracker:**
- Runs IntentEngine against a fixed holdout set (100 labeled turns from Founder Validation, Sprint-029) weekly
- `measure_accuracy(date: date) -> AccuracyMeasurement` — returns intent classification accuracy against holdout
- Stores measurements in `model_drift_history` table
- `DriftAlerter`: if accuracy drops > 5 percentage points from baseline → alert engineering

**OfflineTrainingPipeline:**
- `run(label_set_id: str, model_config: ModelConfig) -> TrainingRun` — orchestrates full fine-tuning cycle
- Steps: 1) build dataset from label_set_id 2) run fine-tuning (on GPU; not on critical path) 3) evaluate on holdout set 4) store model artifact
- Training is decoupled from production: runs on a separate GPU allocation, not the serving pool

**ModelPromoter:**
- `start_ab_test(new_model_id, baseline_model_id, traffic_pct: float = 0.1) -> ABTestHandle` — routes 10% of calls to new model
- After 1000 calls: automatically evaluates results via PromotionGate
- `PromotionGate` criteria (same as Sprint-029 Founder Validation rubric):
  - Intent accuracy ≥ 90% on sampled turns
  - Zero Law-of-Authority violations
  - Zero RBI compliance failures
  - Tone & Empathy average ≥ 3.5/5
- If gate passes → promote new model to 100% via FeatureFlagService (Sprint-026)
- If gate fails → rollback A/B test, keep baseline model, alert engineering with diagnostics

---

## Files Expected to Change

**New:** `src/services/learning-layer/` (all files)
**New:** `tests/unit/services/test_failed_turn_miner.py`, `test_weak_label_generator.py`, `test_model_drift_tracker.py`, `test_model_promoter.py`
**New:** `tests/integration/services/test_learning_pipeline.py`
**Modified:** `src/services/conversation-quality/` — ensure call quality scores are queryable by FailedTurnMiner
**New:** `scripts/run_training_pipeline.py` — CLI entry point for triggering offline training run

---

## Acceptance Criteria

- [ ] `FailedTurnMiner.mine()` returns turns where `factual_accuracy_score < 0.8` from test fixture in MongoDB
- [ ] `WeakLabelGenerator.generate_labels()` produces a label for each failed turn (label_set stored in Postgres)
- [ ] `ModelDriftTracker.measure_accuracy()` returns correct intent accuracy on fixed holdout set
- [ ] `DriftAlerter`: accuracy drops 6 percentage points → alert event emitted
- [ ] `OfflineTrainingPipeline.run()` completes without error on test dataset (may use lightweight mock model)
- [ ] `ModelPromoter.start_ab_test()` creates A/B test with correct traffic split registered in FeatureFlagService
- [ ] `PromotionGate`: all criteria pass → model promoted; any criterion fails → rollback + alert emitted
- [ ] No learning-layer component touches the real-time call path (verified by import analysis)

---

## Required Tests

**Unit:**
- `test_failed_turn_miner_quality_threshold` — turn with factual_accuracy=0.7 → included in results
- `test_failed_turn_miner_quality_pass` — turn with all scores ≥ 0.9 → not included
- `test_weak_label_generator_produces_labels` — 10 failed turns → 10 labeled turns in output
- `test_label_persistence_idempotent` — same label_set_id submitted twice → one record
- `test_drift_tracker_accuracy_measurement` — known holdout set → correct accuracy percentage
- `test_drift_alerter_threshold` — accuracy drop 6pp → DriftAlert emitted
- `test_promotion_gate_pass` — all criteria met → model promoted
- `test_promotion_gate_fail_law_of_authority` — one LOA violation → promotion blocked, rollback triggered

**Integration:**
- `test_learning_pipeline_end_to_end` — seed test quality scores → mine → label → run pipeline → verify TrainingRun record created
- `test_ab_test_traffic_split` — 100 calls with A/B test active → ~10 calls use new model (±3)

---

## Definition of Done

- [ ] All AC items checked
- [ ] FailedTurnMiner queries MongoDB without touching real-time path (import boundary test)
- [ ] Promotion gate uses exact same criteria as Sprint-029 founder validation rubric
- [ ] Training pipeline runs on isolated GPU allocation (not serving pool)
- [ ] Drift tracking job registered in scheduler (runs weekly)
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-031 (final sprint, awaiting all Sprint-032/033/034 complete)

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** FailedTurnMiner uses a `TestMongoDB` Docker fixture; WeakLabelGenerator uses `TestPostgres`; drift tracking uses a fixed in-memory holdout set; OfflineTrainingPipeline uses a lightweight mock model (not a real GPU fine-tuning run) to verify the pipeline control flow; ModelPromoter uses `FakeFeatureFlagService`.

### Files Created

- `src/services/learning-layer/__init__.py`
- `src/services/learning-layer/mining/failed_turn_miner.py`, `selection_criteria.py`
- `src/services/learning-layer/labeling/weak_label_generator.py`, `label_schema.py`
- `src/services/learning-layer/drift/drift_tracker.py`, `drift_alert.py`
- `src/services/learning-layer/training/pipeline.py`, `dataset.py`
- `src/services/learning-layer/promotion/promoter.py`, `gate.py`
- `scripts/run_training_pipeline.py`
- `tests/unit/services/test_failed_turn_miner.py`, `test_weak_label_generator.py`, `test_model_drift_tracker.py`, `test_model_promoter.py`
- `tests/integration/services/test_learning_pipeline.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| MongoDB (`call_quality`) | `TestMongoDB` Docker fixture | Seeded with known quality scores for miner tests |
| Postgres | `TestPostgres` Docker fixture | Training labels, drift history, A/B test records |
| FeatureFlagService | `FakeFeatureFlagService` | Captures `is_enabled` calls for A/B traffic split |
| OfflineTraining GPU | `MockModelTrainer` (returns dummy artifact) | Pipeline control flow test without real GPU |
| EventBus | `FakeEventBus` | Captures DriftAlert and PromotionGate events |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations; no learning-layer import in real-time path |
| Import boundary test | `python -c "from src.services.learning_layer import *; assert no_realtime_imports()"` | Real-time call path modules not imported by learning layer |
| Unit tests | `pytest tests/unit/services/test_failed_turn_miner.py tests/unit/services/test_weak_label_generator.py tests/unit/services/test_model_drift_tracker.py tests/unit/services/test_model_promoter.py` | All pass |
| Pipeline integration | `pytest tests/integration/services/test_learning_pipeline.py` | Seed → mine → label → TrainingRun created |
| Coverage | `pytest --cov=src/services/learning-layer --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `FailedTurnMiner`: turn with `factual_accuracy=0.7` → included; turn with all scores ≥ 0.9 → not included
- `WeakLabelGenerator`: 10 failed turns → 10 `LabeledTurn` records in `TestPostgres`
- Label idempotency: same `label_set_id` twice → 1 record
- `ModelDriftTracker`: known holdout → correct accuracy percentage returned
- `DriftAlerter`: accuracy drop 6pp → `DriftAlert` event emitted to `FakeEventBus`
- `PromotionGate`: all criteria met → `FakeFeatureFlagService.set_enabled(new_model_id, 100%)` called
- `PromotionGate`: 1 LOA violation → promotion blocked; rollback event emitted

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely. The training pipeline requires a **separate GPU allocation** (not the production serving pool). The real-time call path remains completely untouched.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| LearningLayerService | K8s Deployment — `voiceos-ops` | Hosts drift tracker (weekly), miner API, label management |

**Previously deployed services that remain running:**
- All Sprint-004–033 services

**Deployment procedure:**
1. Deploy LearningLayerService to `voiceos-ops` namespace
2. Seed 24h of call quality scores from production MongoDB (FailedTurnMiner test)
3. Trigger drift tracking job manually: `POST /learning/drift/measure` → verify `model_drift_history` row created
4. Mine failed turns from seeded data → verify `FailedTurn` list returned
5. Run A/B test registration: `POST /learning/ab-tests` → verify FeatureFlagService entry created

**Health checks:**
- LearningLayerService: `GET /health/ready` → 200
- Drift tracker: scheduled job registered (verify via cron/scheduler API)
- MongoDB connectivity: miner successfully queries `call_quality` collection

**Integration validation:**
- Drift tracking: weekly job runs → accuracy measurement stored in Postgres
- Failed turn mining: 24h of production quality data → mines turns below threshold
- A/B test: traffic split active → ~10% of calls routed to new model variant
- Promotion gate: A/B test with 1000 calls → gate evaluates automatically

**Rollback procedure:**
- `kubectl rollout undo deployment/learning-layer-service -n voiceos-ops`
- Learning layer is read-only relative to real-time path; rollback has zero impact on call serving

### GPU Node

**Offline training GPU — separate allocation:**

> The OfflineTrainingPipeline runs on a **dedicated GPU allocation**, separate from the production serving pool (Whisper + Qwen2.5 + Veena). It must not share GPU nodes with the serving pool.

| Requirement | Detail |
|---|---|
| GPU allocation | Separate node pool (e.g., `gpu-training` label, separate from `gpu-serving` pool) |
| VRAM | As needed for fine-tuning run (IntentEngine is ONNX/CPU; LLM fine-tuning if applicable) |
| Serving pool isolation | Production serving pool (24,576MB: Whisper + Qwen2.5 + Veena) completely unaffected |
| Training job trigger | `python scripts/run_training_pipeline.py --label-set-id <id>` — manual trigger; not automated in this sprint |

**Training GPU validation:**
- Training job completes on isolated GPU without affecting production serving pool metrics
- `TrainingRun` record created in Postgres with model artifact location

### Infrastructure Validation

**CPU Validation:**
- Import boundary: `check_boundaries.py` confirms zero learning-layer imports in real-time service modules
- Drift tracker: `model_drift_history` row created with correct accuracy for holdout set
- A/B test: FeatureFlagService correctly routes ~10% traffic to new model variant

**GPU Validation:**
- Training job runs on `gpu-training` node pool exclusively (verified via `kubectl describe pod`)
- Production serving pool: no VRAM change, no latency impact during training run
- First-audio p95 remains ≤ 1.5s during training run (confirmed from production traces)

**Networking Validation:**
- LearningLayerService → MongoDB: query latency < 500ms for 24h of call data
- LearningLayerService → FeatureFlagService: A/B test registration < 100ms

### Regression Validation

- Walking skeleton e2e test: passes (real-time call path completely unaffected by learning layer)
- Production serving GPU: VRAM allocation unchanged; serving pool health score remains 1.0
- Promotion gate: A/B test rollback (simulated failure) → FeatureFlagService reverts to baseline model

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] FailedTurnMiner (MongoDB query, quality threshold filtering) implemented
- [ ] WeakLabelGenerator (heuristic labeling, Postgres persistence, idempotency) implemented
- [ ] ModelDriftTracker + DriftAlerter implemented and unit-tested
- [ ] OfflineTrainingPipeline (control flow with mock trainer) implemented
- [ ] ModelPromoter + PromotionGate (same criteria as Sprint-029 rubric) implemented
- [ ] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [ ] Import boundary: learning layer has zero real-time path imports (verified)
- [ ] Pipeline integration test passes end-to-end
- [ ] Coverage ≥ 85%
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] LearningLayerService deployed to `voiceos-ops` and healthy
- [ ] Drift tracking job runs and stores measurements in Postgres
- [ ] FailedTurnMiner successfully queries production MongoDB
- [ ] A/B test registered in FeatureFlagService; traffic split active
- [ ] OfflineTrainingPipeline runs on isolated GPU allocation (not serving pool)
- [ ] Real-time call path completely unaffected (first-audio p95 ≤ 1.5s maintained)
- [ ] All regression tests pass
- [ ] Deployment active; learning layer running as background service alongside Sprint-031 production system

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Sprint-034 adds the Conversation Learning Layer — an offline background service with its **own isolated GPU node** (separate from the Whisper + Qwen2.5 + Veena serving pool). Both documents must reflect the complete final state.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `LearningLayerService` to §8.1 Services table — namespace: `voiceos-ops`
- Update §12 Database Schema: add `failed_turns`, `weak_labels`, `drift_measurements`, `model_versions`, `ab_test_results`, `promotion_records` tables (Postgres); add `conversations` collection note (MongoDB — queried by FailedTurnMiner)
- Add environment variables: `LEARNING_LAYER_URL`, `FEATURE_FLAG_SERVICE_URL`, `TRAINING_GPU_HOST`, `TRAINING_GPU_PORT`, `AB_TEST_TRAFFIC_SPLIT` (§11)
- Add health check: `LearningLayerService` at its voiceos-ops endpoint (§14)
- Update §8.2 Startup Order: LearningLayerService depends on MongoDB, Postgres, FeatureFlagService; starts after serving stack is healthy

### GPU_NODE_STATE.md — Updates This Sprint

> **Two separate GPU node pools now exist.** Add a new §18 Training GPU Node to `GPU_NODE_STATE.md`:

- **Serving Pool** (unchanged): Whisper 6144MB + Qwen2.5-7B 16384MB + Veena 2048MB = 24,576MB — completely unaffected by Sprint-034
- **Training Pool** (new, isolated node): dedicated to OfflineTrainingPipeline; NEVER runs serving models; separate Kubernetes node pool with its own taint

**Training pool entries:**
- OS, NVIDIA driver, CUDA: same baseline as serving pool
- Kubernetes node taint: `training=true:NoSchedule` (separate from `nvidia.com/gpu=true:NoSchedule` serving taint)
- LearningLayerService pod toleration: `training=true:NoSchedule`
- Estimated VRAM for fine-tuning job: depends on base model size; fine-tuning checkpoint dir: `/opt/voiceos-gpu/training/`
- Health check: `OfflineTrainingPipeline` triggered on-demand; not a persistent service

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add LearningLayerService to SERVICE_PORTS map |
| `deployment/cpu/restore.sh` | Add: verify MongoDB `conversations` collection accessible; verify training GPU node registered in K8s node pool |
| `deployment/gpu/model_manifest.yaml` | Add §18 Training Pool section: training GPU taint, checkpoint directory, training pipeline trigger command |
| `deployment/cpu/.env.example` | Add learning layer and training GPU variable descriptions |

### DR Validation

**Learning layer after rebuild:**
```bash
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh

# Verify LearningLayerService can access MongoDB
python3 scripts/validate/learning_layer.py --check mongodb-access
# Expected: FailedTurnMiner can query conversations collection

# Verify drift tracking running
python3 scripts/validate/learning_layer.py --check drift-tracker
# Expected: DriftMeasurement stored in Postgres within 60 seconds
```

**Training GPU node isolation:**
```bash
kubectl get nodes --show-labels | grep "training=true"
# Expected: training GPU node present with correct taint

kubectl describe node <training-gpu-node> | grep Taints
# Expected: Taints: training=true:NoSchedule

# Verify serving pool completely unaffected
python3 scripts/validate/walking_skeleton.py --calls 5
# Expected: first-audio p95 ≤ 1.5s; no GPU contention from training pool
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all 34 sprints of accumulated integration tests pass
# This is the final production system — complete regression is mandatory
```
