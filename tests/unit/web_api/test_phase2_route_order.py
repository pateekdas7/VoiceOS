"""Phase 2 — C4 route-order regression test.

Verifies that W17 explicit routes (/admin/support/*, /admin/impersonation/*,
/admin/security-incidents*, /status) appear in create_web_api()'s route list
BEFORE the AdminPortal catch-all Mount(""). A stray reorder would silently
sink every W17 endpoint into the catch-all.

Reads the source files directly rather than importing — ``api.py`` and
``main.py`` have a long transitive dep tree (JWT, cryptography, httpx,
opentelemetry, pydantic, ...) that is irrelevant to the structural
assertion the test actually makes. Keeping this test dep-free lets it
run on any Python environment, including the CPU node without the full
`requirements.txt` installed.

Run:
    python3 -m pytest tests/unit/web_api/test_phase2_route_order.py --noconftest -v
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
API_PATH = REPO_ROOT / "src" / "services" / "web_api" / "api.py"
MAIN_PATH = REPO_ROOT / "src" / "services" / "web_api" / "main.py"


def _create_web_api_source() -> str:
    """Return the source of ``create_web_api`` as a string."""
    text = API_PATH.read_text()
    m = re.search(r"def create_web_api\(.*?\n(?=\n(?:def |class |\Z))", text, re.S)
    assert m is not None, "create_web_api definition not found in api.py"
    return m.group(0)


def _create_app_source() -> str:
    """Return the source of ``create_app`` as a string."""
    text = MAIN_PATH.read_text()
    m = re.search(r"def create_app\(.*?\n(?=\n(?:def |class |\Z))", text, re.S)
    assert m is not None, "create_app definition not found in main.py"
    return m.group(0)


def test_adminportal_mount_is_last_route_added() -> None:
    """The Mount("", app=admin_portal_app) must be appended after W17 extends."""
    src = _create_web_api_source()

    def pos(pattern: str) -> int:
        m = re.search(pattern, src)
        assert m is not None, f"pattern not found in create_web_api: {pattern!r}"
        return m.start()

    sec = pos(r"_build_security_incident_routes")
    supp = pos(r"_build_support_routes")
    imp = pos(r"_build_impersonation_routes")
    status = pos(r"_build_service_status_route")
    mount = pos(r"routes\.append\(_Mount\(")

    assert sec < mount, "security-incident routes must register before AdminPortal catch-all"
    assert supp < mount, "support routes must register before AdminPortal catch-all"
    assert imp < mount, "impersonation routes must register before AdminPortal catch-all"
    assert status < mount, "status route must register before AdminPortal catch-all"


def test_phase2_h5_wiring_source_has_audit_logger_and_notifier() -> None:
    """main.py must construct IncidentResponse with real notifier + audit_logger."""
    src = _create_app_source()

    assert "incident_audit_logger = AuditLogger(audit_repository)" in src
    assert "RegulatoryNotifier()" in src
    assert "IncidentResponse(" in src
    assert "audit_logger=incident_audit_logger" in src
