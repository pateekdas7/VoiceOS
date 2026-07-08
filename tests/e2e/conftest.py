"""End-to-end test configuration.

E2E tests exercise the complete call path against real or stubbed AI model
backends. They are defined in Sprint-012 (Walking Skeleton — first full call).

This conftest.py establishes the fixture namespace for e2e tests so that
later sprints can add fixtures here without modifying the file structure.

Architecture: V1 Ch9-Ch14 (full call pipeline); DocSuite-02; DocSuite-08.
"""

from __future__ import annotations
