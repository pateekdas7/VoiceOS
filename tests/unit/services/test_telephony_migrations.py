from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = ROOT / "scripts" / "db" / "migrations"
ALEMBIC_DIR = SQL_DIR / "alembic" / "versions"


def test_w2_telephony_migrations_exist_in_order():
    expected = {
        37: "037_telephony_phone_numbers.sql",
        38: "038_call_attempt_lifecycle.sql",
        39: "039_telephony_recordings.sql",
        40: "040_provider_failure_contract.sql",
        41: "041_telephony_call_events.sql",
        42: "042_telephony_event_outbox.sql",
    }
    for number, filename in expected.items():
        path = SQL_DIR / filename
        assert path.exists(), f"missing migration {filename}"
        assert path.read_text(encoding="utf-8").lstrip().startswith("-- Migration"), filename


def test_w2_alembic_revision_chain_is_contiguous():
    revisions = {}
    for number in range(37, 43):
        path = next(ALEMBIC_DIR.glob(f"{number:04d}_*.py"))
        text = path.read_text(encoding="utf-8")
        revision = re.search(r'revision\s*=\s*"([^"]+)"', text)
        down_revision = re.search(r'down_revision\s*=\s*"([^"]+)"', text)
        assert revision and down_revision
        revisions[number] = (revision.group(1), down_revision.group(1))

    assert revisions[37][1] in {"0036", "0036_telephony"} or revisions[37][1].startswith("0036")
    for number in range(38, 43):
        assert revisions[number][1] == revisions[number - 1][0]


def test_w2_event_outbox_migration_has_durable_constraints():
    text = (SQL_DIR / "042_telephony_event_outbox.sql").read_text(encoding="utf-8")
    assert "PRIMARY KEY REFERENCES telephony_call_events(event_id)" in text
    assert "CHECK (status IN ('PENDING','PROCESSING','PUBLISHED','DLQ'))" in text
    assert "CREATE TABLE IF NOT EXISTS telephony_event_dlq" in text
    assert "ON DELETE RESTRICT" in text
    assert "idx_telephony_event_outbox_ready" in text
