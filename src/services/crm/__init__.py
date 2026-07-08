"""CRM — authoritative customer/party records + CustomerContext assembly (V5 Ch3/Ch4).

The CRM is the Law-of-Authority source of truth for customer identity,
party relationships, language/DND preference, and consent — combined with
Collections data by ``CustomerContextAssembler`` into the sealed,
immutable ``CustomerContext`` the conversation engine consumes.

Architecture: V5 Ch3 (Customer CRM), Ch4 (CRM/CustomerContext); Invariant RI-5.
"""

from __future__ import annotations

from .context_assembler import CustomerContextAssembler, CustomerNotFoundError
from .importer import CustomerImporter, ImportResult
from .party import PartyService
from .repository import CRMRepositories
from .service import CustomerService

__all__ = [
    "CRMRepositories",
    "CustomerContextAssembler",
    "CustomerImporter",
    "CustomerNotFoundError",
    "CustomerService",
    "ImportResult",
    "PartyService",
]
