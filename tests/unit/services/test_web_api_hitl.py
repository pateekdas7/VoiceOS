"""Unit tests for Client -> Live Calls -> Escalations (HITL) routes (ADR-005 Sec 12.2).

All tests run fully in-process against a real Starlette TestClient with fake
HITLQueueRepositoryPort/HITLDecisionRepositoryPort implementations backing
real HITLQueue/OverrideLogger service objects -- no live Postgres required
(Phase 1), same convention as test_web_api_campaigns.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from src.libs.contracts.models.hitl import HITLDecision, HITLItem, HITLItemStatus, HITLPriority
from src.services.authz.roles import ROLE_PERMISSIONS
from src.services.authz.roles import Role as AuthzRole
from src.services.hitl.override_logger import OverrideLogger
from src.services.hitl.queue import HITLQueue
from src.services.platform_admin.service import PlatformAdminService
from src.services.user_management.invitation import InvitationService
from src.services.user_management.service import UserService
from src.services.web_api.api import create_web_api
from src.services.web_api.session import WebSessionCodec

FRONTEND_URL = "https://app.voiceos.test"
BFF_URL = "https://bff.voiceos.test"
TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


class _FakeHITLQueueRepository:
    def __init__(self) -> None:
        self._store: dict[str, HITLItem] = {}

    def create(self, item: HITLItem) -> HITLItem:
        self._store[item.hitl_item_id] = item
        return item

    def get(self, tenant_id: str, hitl_item_id: str) -> HITLItem | None:
        item = self._store.get(hitl_item_id)
        if item is None or item.tenant_id != tenant_id:
            return None
        return item

    def find_pending(self, tenant_id: str) -> tuple[HITLItem, ...]:
        return tuple(
            i for i in self._store.values() if i.tenant_id == tenant_id and i.status == HITLItemStatus.PENDING
        )

    def find_open(self, tenant_id: str | None = None) -> tuple[HITLItem, ...]:
        return tuple(
            i
            for i in self._store.values()
            if (tenant_id is None or i.tenant_id == tenant_id)
            and i.status in (HITLItemStatus.PENDING, HITLItemStatus.CLAIMED)
        )

    def claim(self, tenant_id: str, hitl_item_id: str, supervisor_id: str, claimed_at: datetime) -> None:
        item = self._store[hitl_item_id]
        self._store[hitl_item_id] = item.model_copy(
            update={"status": HITLItemStatus.CLAIMED, "claimed_by": supervisor_id, "claimed_at": claimed_at}
        )

    def resolve(self, tenant_id: str, hitl_item_id: str, resolved_at: datetime) -> None:
        item = self._store[hitl_item_id]
        self._store[hitl_item_id] = item.model_copy(
            update={"status": HITLItemStatus.RESOLVED, "resolved_at": resolved_at}
        )

    def mark_sla_breached(self, tenant_id: str, hitl_item_id: str) -> None:
        item = self._store[hitl_item_id]
        self._store[hitl_item_id] = item.model_copy(update={"sla_breached": True})


class _FakeHITLDecisionRepository:
    def __init__(self) -> None:
        self._store: list[HITLDecision] = []

    def create(self, decision: HITLDecision) -> HITLDecision:
        self._store.append(decision)
        return decision

    def find_by_item(self, tenant_id: str, hitl_item_id: str) -> tuple[HITLDecision, ...]:
        return tuple(d for d in self._store if d.tenant_id == tenant_id and d.hitl_item_id == hitl_item_id)


class _NoopPlatformUserRepository:
    def create(self, user: Any) -> Any:
        return user

    def get(self, platform_user_id: str) -> None:
        return None

    def find_by_email(self, email: str) -> None:
        return None

    def list_all(self) -> tuple[Any, ...]:
        return ()

    def set_active_status(self, platform_user_id: str, *, is_active: bool) -> int:
        return 0


class _NoopUserRepository:
    def create_user(self, user: Any) -> Any:
        return user

    def assign_role(self, assignment: Any) -> Any:
        return assignment

    def get_user(self, tenant_id: Any, user_id: str) -> None:
        return None

    def find_user_by_email(self, tenant_id: Any, email: str) -> None:
        return None

    def list_users(self, tenant_id: Any) -> tuple[Any, ...]:
        return ()

    def set_active_status(self, tenant_id: Any, user_id: str, *, is_active: bool) -> int:
        return 0

    def get_role(self, tenant_id: Any, role_id: str) -> Any:
        return None

    def get_role_by_name(self, tenant_id: Any, name: str) -> Any:
        return None

    def list_roles(self, tenant_id: Any) -> tuple[Any, ...]:
        return ()

    def create_role(self, role: Any) -> Any:
        return role


class _NoopInvitationRepository:
    def create(self, invitation: Any) -> Any:
        return invitation

    def get_by_token_hash(self, token_hash: str) -> None:
        return None

    def mark_status(self, invitation_id: str, status: str, *, accepted_at: Any = None) -> int:
        return 0


class _NoopGoogleOAuth:
    def build_authorize_url(self, *, state: str, redirect_uri: str) -> str:
        return "https://accounts.google.com/fake"

    async def resolve_verified_email(self, *, code: str, redirect_uri: str) -> Any:
        raise NotImplementedError


@pytest.fixture
def session_codec() -> WebSessionCodec:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return WebSessionCodec(private_key, private_key.public_key())


@pytest.fixture
def queue_repo() -> _FakeHITLQueueRepository:
    return _FakeHITLQueueRepository()


@pytest.fixture
def decision_repo() -> _FakeHITLDecisionRepository:
    return _FakeHITLDecisionRepository()


@pytest.fixture
def app_client(
    session_codec: WebSessionCodec,
    queue_repo: _FakeHITLQueueRepository,
    decision_repo: _FakeHITLDecisionRepository,
) -> TestClient:
    platform_admin = PlatformAdminService(_NoopPlatformUserRepository())
    invitation_service = InvitationService(_NoopInvitationRepository(), _NoopUserRepository())
    user_service = UserService(_NoopUserRepository(), invitation_service)
    hitl_queue = HITLQueue(queue_repo)
    override_logger = OverrideLogger(decision_repo, queue_repo)

    app = create_web_api(
        session_codec=session_codec,
        google_oauth=_NoopGoogleOAuth(),
        platform_admin=platform_admin,
        user_service=user_service,
        hitl_queue=hitl_queue,
        override_logger=override_logger,
        frontend_base_url=FRONTEND_URL,
        bff_public_url=BFF_URL,
        cookie_secure=False,
    )
    return TestClient(app)


def _tenant_token(codec: WebSessionCodec, tenant_id: str = TENANT_A, role: str = "SUPERVISOR") -> str:
    permissions = tuple(ROLE_PERMISSIONS.get(AuthzRole(role), frozenset()))
    return codec.encode(
        actor_kind="tenant", subject="sup-1", role=role, permissions=permissions, email="sup@tenant.com",
        tenant_id=tenant_id,
    )


def _item(tenant_id: str, hitl_item_id: str, priority: HITLPriority = HITLPriority.HIGH) -> HITLItem:
    now = datetime.now(UTC)
    return HITLItem(
        hitl_item_id=hitl_item_id,
        tenant_id=tenant_id,  # type: ignore[arg-type]
        call_id="call-1",
        reason="disputed debt",
        priority=priority,
        enqueued_at=now,
        sla_deadline_at=now,
    )


class TestListQueue:
    def test_requires_auth(self, app_client: TestClient) -> None:
        response = app_client.get("/hitl/queue")
        assert response.status_code == 401

    def test_agent_lacks_permission(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.get(
            "/hitl/queue", cookies={"voiceos_session": _tenant_token(session_codec, role="AGENT")}
        )
        assert response.status_code == 403

    def test_scoped_to_own_tenant_only(
        self, app_client: TestClient, session_codec: WebSessionCodec, queue_repo: _FakeHITLQueueRepository
    ) -> None:
        queue_repo._store["a"] = _item(TENANT_A, "a")
        queue_repo._store["b"] = _item(TENANT_B, "b")

        response = app_client.get("/hitl/queue", cookies={"voiceos_session": _tenant_token(session_codec)})
        assert response.status_code == 200
        ids = {i["hitl_item_id"] for i in response.json()}
        assert ids == {"a"}

    def test_resolved_items_excluded(
        self, app_client: TestClient, session_codec: WebSessionCodec, queue_repo: _FakeHITLQueueRepository
    ) -> None:
        item = _item(TENANT_A, "a")
        queue_repo._store["a"] = item.model_copy(update={"status": HITLItemStatus.RESOLVED})

        response = app_client.get("/hitl/queue", cookies={"voiceos_session": _tenant_token(session_codec)})
        assert response.json() == []


class TestClaimNext:
    def test_claims_highest_priority_first(
        self, app_client: TestClient, session_codec: WebSessionCodec, queue_repo: _FakeHITLQueueRepository
    ) -> None:
        queue_repo._store["medium"] = _item(TENANT_A, "medium", priority=HITLPriority.MEDIUM)
        queue_repo._store["critical"] = _item(TENANT_A, "critical", priority=HITLPriority.CRITICAL)

        response = app_client.post(
            "/hitl/queue/claim-next", cookies={"voiceos_session": _tenant_token(session_codec)}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["hitl_item_id"] == "critical"
        assert body["status"] == "CLAIMED"
        assert body["claimed_by"] == "sup-1"

    def test_empty_queue_returns_404(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/hitl/queue/claim-next", cookies={"voiceos_session": _tenant_token(session_codec)}
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "QUEUE_EMPTY"


class TestRecordDecision:
    def test_agent_cannot_decide(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/hitl/items/a/decision",
            json={"decision": "APPROVE", "rationale": "looks fine"},
            cookies={"voiceos_session": _tenant_token(session_codec, role="AGENT")},
        )
        assert response.status_code == 403

    def test_empty_rationale_returns_422(
        self, app_client: TestClient, session_codec: WebSessionCodec, queue_repo: _FakeHITLQueueRepository
    ) -> None:
        queue_repo._store["a"] = _item(TENANT_A, "a")
        response = app_client.post(
            "/hitl/items/a/decision",
            json={"decision": "APPROVE", "rationale": ""},
            cookies={"voiceos_session": _tenant_token(session_codec)},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_missing_decision_returns_422(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/hitl/items/a/decision",
            json={"rationale": "looks fine"},
            cookies={"voiceos_session": _tenant_token(session_codec)},
        )
        assert response.status_code == 422

    def test_full_happy_path_resolves_item(
        self, app_client: TestClient, session_codec: WebSessionCodec, queue_repo: _FakeHITLQueueRepository
    ) -> None:
        queue_repo._store["a"] = _item(TENANT_A, "a")
        response = app_client.post(
            "/hitl/items/a/decision",
            json={"decision": "OVERRIDE", "rationale": "customer provided valid dispute evidence"},
            cookies={"voiceos_session": _tenant_token(session_codec)},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["decision"] == "OVERRIDE"
        assert body["supervisor_id"] == "sup-1"
        assert queue_repo._store["a"].status == HITLItemStatus.RESOLVED
