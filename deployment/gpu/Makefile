# VoiceOS GPU Runtime — Local Development Makefile
#
# Prerequisites:
#   pip install -r deployment/gpu/requirements.cpu.txt
#
# Quick start (all three mock services):
#   make dev
#
# Run tests:
#   make test
#
# Run tests with live STT (tiny model, CPU):
#   STT_MOCK=false make test

SHELL := /bin/bash
GPU_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
PYTHON  := python3

# Ports for dev servers (offset from prod to avoid conflicts)
DEV_STT_PORT ?= 8100
DEV_LLM_PORT ?= 8000
DEV_TTS_PORT ?= 8200

# ── Local dev servers ──────────────────────────────────────────────────────────

.PHONY: dev-stt
dev-stt:
	@echo "[dev] Starting mock STT on :$(DEV_STT_PORT)"
	VOICEOS_MODE=dev STT_SERVICE_PORT=$(DEV_STT_PORT) \
	$(PYTHON) $(GPU_DIR)services/stt/server.py \
		--mock --port $(DEV_STT_PORT)

.PHONY: dev-stt-cpu
dev-stt-cpu:
	@echo "[dev] Starting Whisper tiny (CPU) on :$(DEV_STT_PORT)"
	VOICEOS_MODE=dev STT_SERVICE_PORT=$(DEV_STT_PORT) \
	$(PYTHON) $(GPU_DIR)services/stt/server.py \
		--device cpu --model-path tiny --compute-type int8 --port $(DEV_STT_PORT)

.PHONY: dev-llm
dev-llm:
	@echo "[dev] Starting mock LLM on :$(DEV_LLM_PORT)"
	$(PYTHON) $(GPU_DIR)services/llm/mock_server.py --port $(DEV_LLM_PORT)

.PHONY: dev-tts
dev-tts:
	@echo "[dev] Starting mock TTS on :$(DEV_TTS_PORT)"
	VOICEOS_MODE=dev TTS_SERVICE_PORT=$(DEV_TTS_PORT) \
	$(PYTHON) $(GPU_DIR)services/tts/server.py \
		--mock --port $(DEV_TTS_PORT)

.PHONY: dev
dev:
	@echo "[dev] Starting all GPU services in mock mode..."
	@echo "      STT  → http://localhost:$(DEV_STT_PORT)"
	@echo "      LLM  → http://localhost:$(DEV_LLM_PORT)"
	@echo "      TTS  → http://localhost:$(DEV_TTS_PORT)"
	$(MAKE) -j3 dev-stt dev-llm dev-tts

# ── Tests ──────────────────────────────────────────────────────────────────────

.PHONY: test
test:
	@echo "[test] Running GPU runtime test suite..."
	cd $(GPU_DIR)/../.. && \
	VOICEOS_MODE=dev \
	STT_SERVICE_PORT=$(DEV_STT_PORT) \
	LLM_SERVICE_PORT=$(DEV_LLM_PORT) \
	TTS_SERVICE_PORT=$(DEV_TTS_PORT) \
	$(PYTHON) -m pytest deployment/gpu/tests/ -v --tb=short 2>&1

.PHONY: test-stt
test-stt:
	cd $(GPU_DIR)/../.. && VOICEOS_MODE=dev \
	$(PYTHON) -m pytest deployment/gpu/tests/test_stt.py -v 2>&1

.PHONY: test-llm
test-llm:
	cd $(GPU_DIR)/../.. && VOICEOS_MODE=dev \
	$(PYTHON) -m pytest deployment/gpu/tests/test_llm.py -v 2>&1

.PHONY: test-tts
test-tts:
	cd $(GPU_DIR)/../.. && VOICEOS_MODE=dev \
	$(PYTHON) -m pytest deployment/gpu/tests/test_tts.py -v 2>&1

.PHONY: test-pipeline
test-pipeline:
	@echo "[test] Running full STT→LLM→TTS pipeline test..."
	cd $(GPU_DIR)/../.. && VOICEOS_MODE=dev \
	$(PYTHON) -m pytest deployment/gpu/tests/test_pipeline.py -v --log-cli-level=INFO 2>&1

# ── Validation (against live services) ────────────────────────────────────────

.PHONY: validate
validate:
	@echo "[validate] Running latency validation against live services..."
	$(PYTHON) $(GPU_DIR)validate_latency.py --test all

# ── Install ────────────────────────────────────────────────────────────────────

.PHONY: install
install:
	pip install -r $(GPU_DIR)requirements.cpu.txt

.PHONY: install-prod
install-prod:
	pip install -r $(GPU_DIR)requirements.txt

# ── Health check ───────────────────────────────────────────────────────────────

.PHONY: health
health:
	@curl -sf http://localhost:$(DEV_STT_PORT)/health/ready && echo "STT: OK" || echo "STT: DOWN"
	@curl -sf http://localhost:$(DEV_LLM_PORT)/health && echo "LLM: OK" || echo "LLM: DOWN"
	@curl -sf http://localhost:$(DEV_TTS_PORT)/health/ready && echo "TTS: OK" || echo "TTS: DOWN"

.PHONY: help
help:
	@echo "VoiceOS GPU Runtime — Local Dev Commands"
	@echo ""
	@echo "  make install       Install CPU/dev dependencies"
	@echo "  make dev           Start all services in mock mode (parallel)"
	@echo "  make dev-stt       Start mock STT server"
	@echo "  make dev-stt-cpu   Start STT with Whisper tiny on CPU"
	@echo "  make dev-llm       Start mock LLM server"
	@echo "  make dev-tts       Start mock TTS server"
	@echo "  make test          Run full test suite"
	@echo "  make test-pipeline Run end-to-end pipeline test"
	@echo "  make health        Check running service health"
	@echo "  make validate      Run latency validation"
