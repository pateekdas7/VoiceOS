import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = ROOT / "scripts" / "db" / "migrations"
ALEMBIC_DIR = SQL_DIR / "alembic" / "versions"

W2_ALEMBIC = [
    ("0043", "0043_telephony_phone_numbers.py", "0038"),
    ("0044", "0044_call_attempt_lifecycle.py", "0043"),
    ("0045", "0045_telephony_recordings.py", "0044"),
    ("0046", "0046_provider_failure_contract.py", "0045"),
    ("0047", "0047_telephony_call_events.py", "0046"),
    ("0048", "0048_telephony_event_outbox.py", "0047"),
]

def _revision_metadata(path: Path) -> tuple[str, str | None]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in {"revision", "down_revision"}:
                values[node.targets[0].id] = ast.literal_eval(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in {"revision", "down_revision"}:
                values[node.target.id] = ast.literal_eval(node.value)
    return values["revision"], values["down_revision"]

def test_w2_telephony_migrations_exist_in_order():
    expected = {
        37: "037_telephony_phone_numbers.sql",
        38: "038_call_attempt_lifecycle.sql",
        39: "039_telephony_recordings.sql",
        40: "040_provider_failure_contract.sql",
        41: "041_telephony_call_events.sql",
        42: "042_telephony_event_outbox.sql",
    }
    for _number, filename in expected.items():
        path = SQL_DIR / filename
        assert path.exists(), f"missing migration {filename}"
        assert path.read_text(encoding="utf-8").lstrip().startswith("-- Migration"), filename

def test_w2_alembic_revision_chain_is_contiguous():
    for revision, filename, down_revision in W2_ALEMBIC:
        path = ALEMBIC_DIR / filename
        assert path.exists(), filename
        revision_value, down_value = _revision_metadata(path)
        assert revision_value == revision
        assert down_value == down_revision

def test_alembic_revision_ids_are_unique_and_down_revisions_exist():
    metadata = {}
    for path in ALEMBIC_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        revision, _down_revision = _revision_metadata(path)
        assert revision not in metadata, f"duplicate Alembic revision {revision}: {metadata.get(revision)} and {path.name}"
        metadata[revision] = path.name
    for _revision, path in metadata.items():
        down = _revision_metadata(ALEMBIC_DIR / path)[1]
        if down is not None:
            assert down in metadata, f"{path} points to missing down_revision {down}"

def test_alembic_graph_has_one_head_and_no_unintended_branches():
    metadata = {}
    children = {}
    for path in ALEMBIC_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        revision, down_revision = _revision_metadata(path)
        metadata[revision] = down_revision
        if down_revision is not None:
            children.setdefault(down_revision, []).append(revision)
    heads = sorted(set(metadata) - set(children))
    assert heads == ["0048"], heads
    for parent, child_revisions in children.items():
        assert len(child_revisions) == 1, f"unexpected Alembic branch at {parent}: {child_revisions}"

def test_w2_alembic_graph_preserves_existing_0037_0038_history():
    assert _revision_metadata(ALEMBIC_DIR / "0037_compliance_violations.py") == ("0037", "0036")
    assert _revision_metadata(ALEMBIC_DIR / "0038_require_crm_match.py") == ("0038", "0037")
    assert _revision_metadata(ALEMBIC_DIR / "0043_telephony_phone_numbers.py") == ("0043", "0038")

def test_w2_event_outbox_migration_has_durable_constraints():
    text = (SQL_DIR / "042_telephony_event_outbox.sql").read_text(encoding="utf-8")
    assert "PRIMARY KEY REFERENCES telephony_call_events(event_id)" in text
    assert "CHECK (status IN ('PENDING','PROCESSING','PUBLISHED','DLQ'))" in text
    assert "CREATE TABLE IF NOT EXISTS telephony_event_dlq" in text
    assert "ON DELETE RESTRICT" in text
    assert "idx_telephony_event_outbox_ready" in text
