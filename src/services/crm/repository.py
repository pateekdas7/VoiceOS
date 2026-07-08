"""CRMRepositories — bundles the CRM domain's repositories (V5 Ch3).

Sprint-022.md describes this module as "CustomerRepository wrapper —
delegates to src/libs/repositories/". The concrete ``CustomerRepository`` and
``PartyRepository`` already exist there with their SQL/tenant-scoping logic
in full (Sprint-014/022) — this module does not re-implement any of it, it
only bundles the pair that ``CustomerService``/``CustomerContextAssembler``
need into one constructor argument, mirroring the "one façade, several
repositories" shape already used by ``TenantService``.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.libs.repositories.customer import CustomerRepository
from src.libs.repositories.party import PartyRepository


@dataclass(frozen=True)
class CRMRepositories:
    """The CRM domain's persistence dependencies, bundled for convenience."""

    customer: CustomerRepository
    party: PartyRepository
