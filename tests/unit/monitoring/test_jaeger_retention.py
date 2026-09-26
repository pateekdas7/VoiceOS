"""Configuration tests for the deployed Jaeger/OTel retention contract."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
JAEGER_MANIFEST = ROOT / "infra/k8s/observability/otel-jaeger.yaml"
JAEGER_REFERENCE = ROOT / "monitoring/tracing/jaeger.yml"
DEPLOY_SCRIPT = ROOT / "infra/k8s/observability/deploy.sh"

def test_jaeger_uses_badger_storage_and_7_day_ttl() -> None:
    manifest = JAEGER_MANIFEST.read_text()
    reference = JAEGER_REFERENCE.read_text()
    assert 'image: "jaegertracing/all-in-one:1.60"' in manifest
    assert "name: SPAN_STORAGE_TYPE" in manifest
    assert 'value: "badger"' in manifest
    assert "--badger.span-store-ttl=168h0m0s" in manifest
    assert re.search(r"retention:\s*\n\s+max_age:\s+168h\b", reference)

def test_retention_setting_is_attached_to_actual_jaeger_deployment() -> None:
    manifest = JAEGER_MANIFEST.read_text()
    jaeger_block = manifest.split("      containers:", 1)[1].split("      volumes:", 1)[0]
    assert "name: jaeger" in jaeger_block
    assert "--badger.span-store-ttl=168h0m0s" in jaeger_block

def test_storage_paths_match_retention_mechanism() -> None:
    manifest = JAEGER_MANIFEST.read_text()
    assert 'value: "/badger/data"' in manifest
    assert 'value: "/badger/key"' in manifest
    assert "mountPath: /badger" in manifest
    assert "type: badger" in JAEGER_REFERENCE.read_text()

def test_jaeger_reference_is_generated_into_the_deployed_configmap() -> None:
    script = DEPLOY_SCRIPT.read_text()
    assert "kubectl create configmap jaeger-config" in script
    assert "--from-file=\"" + "${MON_DIR}/tracing/jaeger.yml" + "\"" in script

def test_no_unimplemented_purge_job_is_claimed() -> None:
    reference = JAEGER_REFERENCE.read_text()
    manifest = JAEGER_MANIFEST.read_text()
    assert "jaeger-badger-purge" not in reference
    assert "jaeger-badger-purge" not in manifest
