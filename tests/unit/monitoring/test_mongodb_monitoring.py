from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[3]
PROMETHEUS = ROOT / "monitoring" / "prometheus" / "prometheus.yml"
ALERTS = ROOT / "monitoring" / "prometheus" / "alert_rules" / "service_health.yml"
COMPOSE = ROOT / "docker-compose.yml"
EXPORTER = ROOT / "infra" / "k8s" / "observability" / "mongodb-exporter.yaml"
NETWORK_POLICY = ROOT / "infra" / "k8s" / "observability" / "networkpolicy.yaml"

def test_prometheus_scrapes_mongodb_exporter() -> None:
    config = yaml.safe_load(PROMETHEUS.read_text())
    job = next(x for x in config["scrape_configs"] if x["job_name"] == "mongodb-exporter")
    assert job["static_configs"][0]["targets"] == ["mongodb-exporter:9216"]
    assert job["static_configs"][0]["labels"] == {"service": "mongodb", "component": "database"}

def test_mongodb_alerts_cover_exporter_and_database_health() -> None:
    config = yaml.safe_load(ALERTS.read_text())
    rules = {r["alert"]: r for g in config["groups"] for r in g["rules"]}
    assert rules["VoiceOSMongoDBExporterDown"]["expr"] == 'up{job="mongodb-exporter"} == 0'
    assert rules["VoiceOSMongoDBUnavailable"]["expr"] == 'mongodb_up{job="mongodb-exporter"} == 0'

def test_kubernetes_exporter_uses_runtime_secret_and_metrics_service() -> None:
    deployment, service = list(yaml.safe_load_all(EXPORTER.read_text()))
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "percona/mongodb_exporter:0.53"
    assert container["env"][0]["valueFrom"]["secretKeyRef"] == {"name": "mongodb-exporter-credentials", "key": "MONGODB_URI"}
    assert service["metadata"]["name"] == "mongodb-exporter"
    assert service["spec"]["ports"][0]["port"] == 9216

def test_compose_defines_mongodb_exporter() -> None:
    config = yaml.safe_load(COMPOSE.read_text())
    exporter = config["services"]["mongodb-exporter"]
    assert exporter["image"] == "percona/mongodb_exporter:0.53"
    assert "--collect-all" in exporter["command"]
    assert "--mongodb.uri=mongodb://mongodb:27017/?directConnection=true" in exporter["command"]

def test_network_policy_allows_prometheus_and_mongodb_paths() -> None:
    docs = list(yaml.safe_load_all(NETWORK_POLICY.read_text()))
    prometheus = next(x for x in docs if x["metadata"]["name"] == "prometheus")
    exporter = next(x for x in docs if x["metadata"]["name"] == "mongodb-exporter")
    assert any(port["port"] == 9216 for rule in prometheus["spec"]["egress"] for port in rule["ports"])
    assert any(rule.get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name") == "prometheus" and any(port["port"] == 9216 for port in rule["ports"]) for rule in exporter["spec"]["ingress"])
    assert any(rule.get("namespaceSelector", {}).get("matchLabels", {}).get("kubernetes.io/metadata.name") == "voiceos-data" and any(port["port"] == 27017 for port in rule["ports"]) for rule in exporter["spec"]["egress"])
