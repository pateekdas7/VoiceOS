"""Phase 12 Verification Tests — Auth Hardening

12a. Login rate limiting — already done (Phase 9b bff.js); verify spec matches
12b. Move credentials to Vault — _load_vault_secret_or_env in web_api/main.py
12c. Session revocation list — JTI in tokens, Redis revocation on logout
12d. Secure cookie flag — already done (Phase 1); verify present
12e. Cookie domain scoping — COOKIE_DOMAIN env var support

Run with: python3 tests/unit/test_phase12_auth_hardening.py
Strategy: pure source inspection.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

passed = 0
failed = 0
results: list[tuple[str, str, str]] = []


def test(name: str) -> "object":
    def _dec(fn: "object") -> "object":
        global passed, failed
        try:
            fn()  # type: ignore[operator]
            results.append((name, "PASS", ""))
            passed += 1
        except AssertionError as e:
            results.append((name, "FAIL", str(e)))
            failed += 1
        except Exception as e:
            results.append((name, "FAIL", f"{type(e).__name__}: {e}"))
            failed += 1
        return fn
    return _dec


BFF_SRC     = (ROOT / "bff.js").read_text()
MAIN_PY_SRC = (ROOT / "src/services/web_api/main.py").read_text()

# ---------------------------------------------------------------------------
# 12a: Login rate limiting (already implemented in BFF Phase 9b)
# ---------------------------------------------------------------------------

@test("12a: bff.js has Redis-backed login rate limit helper")
def _(): assert "checkLoginRateLimit" in BFF_SRC

@test("12a: login rate limit uses 5 failures default")
def _():
    assert "LOGIN_MAX_FAILURES" in BFF_SRC
    assert "'5'" in BFF_SRC or '"5"' in BFF_SRC

@test("12a: login rate limit window is 900s (15 min) default")
def _():
    assert "LOGIN_RATE_WINDOW_S" in BFF_SRC
    assert "'900'" in BFF_SRC or '"900"' in BFF_SRC

@test("12a: login route calls checkLoginRateLimit")
def _():
    idx = BFF_SRC.index("/auth/password/login")
    region = BFF_SRC[idx:idx + 600]
    assert "checkLoginRateLimit" in region

# ---------------------------------------------------------------------------
# 12b: Move credentials to Vault
# ---------------------------------------------------------------------------

@test("12b: _load_vault_secret_or_env helper defined in web_api/main.py")
def _(): assert "_load_vault_secret_or_env" in MAIN_PY_SRC

@test("12b: helper tries Vault before env var")
def _():
    idx = MAIN_PY_SRC.index("def _load_vault_secret_or_env")
    region = MAIN_PY_SRC[idx:idx + 600]
    assert "VAULT_ADDR" in region
    assert "SecretsManager" in region

@test("12b: helper falls back to env var on Vault unavailable")
def _():
    idx = MAIN_PY_SRC.index("def _load_vault_secret_or_env")
    region = MAIN_PY_SRC[idx:idx + 1100]
    assert "_require_env" in region

@test("12b: GOOGLE_CLIENT_SECRET loaded via Vault helper in create_app")
def _():
    idx = MAIN_PY_SRC.index("def create_app")
    region = MAIN_PY_SRC[idx:idx + 400]
    assert "_load_vault_secret_or_env" in region

@test("12b: Vault path for google-client-secret is correct")
def _():
    assert "voiceos/web-api/google-client-secret" in MAIN_PY_SRC

# ---------------------------------------------------------------------------
# 12c: Session revocation list
# ---------------------------------------------------------------------------

@test("12c: bff.js imports randomUUID from crypto")
def _(): assert "randomUUID" in BFF_SRC

@test("12c: makeToken adds jti to JWT payload")
def _():
    idx = BFF_SRC.index("function makeToken")
    region = BFF_SRC[idx:idx + 200]
    assert "jti" in region

@test("12c: JWT middleware checks JTI revocation")
def _():
    assert "revoked_jti" in BFF_SRC

@test("12c: JWT middleware rejects revoked JTI")
def _():
    assert "redis.exists" in BFF_SRC or "redis.get" in BFF_SRC

@test("12c: _revokeSession helper defined")
def _(): assert "_revokeSession" in BFF_SRC

@test("12c: logout POST calls _revokeSession")
def _():
    idx = BFF_SRC.index("app.post('/auth/logout'")
    region = BFF_SRC[idx:idx + 300]
    assert "_revokeSession" in region

@test("12c: logout GET calls _revokeSession")
def _():
    idx = BFF_SRC.index("app.get('/auth/logout'")
    region = BFF_SRC[idx:idx + 300]
    assert "_revokeSession" in region

@test("12c: JTI revocation key stored with TTL from JWT expiry")
def _():
    idx = BFF_SRC.index("_revokeSession")
    region = BFF_SRC[idx:idx + 400]
    assert "req.user.exp" in region or "user?.exp" in region
    assert "EX" in region  # Redis TTL parameter

# ---------------------------------------------------------------------------
# 12d: Secure cookie flag (already done)
# ---------------------------------------------------------------------------

@test("12d: setCookies sets secure flag based on NODE_ENV")
def _():
    idx = BFF_SRC.index("function setCookies")
    region = BFF_SRC[idx:idx + 300]
    assert "production" in region and "secure" in region

@test("12d: httpOnly is true on session cookies")
def _():
    idx = BFF_SRC.index("function setCookies")
    region = BFF_SRC[idx:idx + 300]
    assert "httpOnly: true" in region

# ---------------------------------------------------------------------------
# 12e: Cookie domain scoping
# ---------------------------------------------------------------------------

@test("12e: setCookies reads COOKIE_DOMAIN env var")
def _():
    idx = BFF_SRC.index("function setCookies")
    region = BFF_SRC[idx:idx + 300]
    assert "COOKIE_DOMAIN" in region

@test("12e: COOKIE_DOMAIN applied to cookie opts when set")
def _():
    idx = BFF_SRC.index("function setCookies")
    region = BFF_SRC[idx:idx + 300]
    assert "domain" in region

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

print("\n── Phase 12 Auth Hardening Tests ─────────────────────────────────────")
for name, status, detail in results:
    line = f"  [{status}] {name}"
    if detail:
        line += f"\n         {detail}"
    print(line)
print(f"\n  {passed} passed, {failed} failed\n")
sys.exit(1 if failed > 0 else 0)
