# CLAUDE.md — VoiceOS v2

## Purpose

This file is the permanent **engineering constitution** for the VoiceOS project. Every implementation task must follow the rules defined here. If any instruction from a user conflicts with this document, **ask for clarification** rather than silently violating the architecture.

---

## Project

**Name:** VoiceOS v2
**Purpose:** Enterprise-grade AI Voice Operating System for collections, customer engagement, and conversational automation.

The architecture is already designed. The objective is **implementation, testing, validation, and production deployment**. Do **NOT** redesign the architecture unless explicitly instructed through an approved Architecture Decision Record (ADR).

---

## Architecture Authority

The following documents are authoritative.

- **Volume 1 — Core Voice Architecture.** Runtime pipeline · audio processing · streaming · GPU Scheduler · STT · LLM · TTS · Playback · runtime contracts. *(incl. Ch.26 Runtime Deployment Topology & Compute Architecture.)*
- **Volume 2 — Conversation Intelligence.** Intent Engine · Strategy Engine · Negotiation Engine · Goal Planner · Policy Engine · Risk Engine · Working Memory · Relationship Memory · ResponsePlan · DecisionEnvelope · Emotion Intelligence.
- **Volume 3 — Reliability Architecture.** Redis · Persistence · Event sourcing · Recovery · Replay · Idempotency · Health · Monitoring · Tracing · Disaster Recovery.
- **Volume 4 — Compliance & Security.** Compliance · Policy · Security · Privacy · RBAC · Audit · AI Governance.
- **Volume 5 — SaaS Platform.** CRM · Billing · Multi-tenancy · Analytics · Campaigns · Customer Management · Admin Portal.
- **Volume 6 — Developer Handbook.** Coding standards · Testing · Repository structure · CI/CD · Engineering workflow · Documentation standards.
- **Volume 7 — Operations & Scaling.** Deployment · Kubernetes · GPU Fleet · Monitoring · Scaling · Capacity Planning · Production Operations.

*(Companion reference: the Documentation Suite, Documents 1–12, consolidates and indexes Volumes 1–7.)*

---

## Engineering Principles

Always follow the architecture. Never redesign components during implementation. Never introduce hidden architectural changes. Never bypass an architectural layer.

**If implementation requires changing architecture:** STOP → explain why → recommend an ADR → wait for approval.

---

## Law of Authority

- Authoritative data always wins.
- The LLM never invents facts.
- Business logic never lives inside prompts.
- Customer facts come from authoritative systems.
- Derived state must never overwrite authoritative state.

---

## Implementation Rules

- Build only the requested sprint.
- Never implement future sprints.
- Never skip dependencies.
- Never leave partially implemented features.
- Never create placeholder implementations.
- Finish one sprint completely before beginning another.

---

## Code Quality

Write production-quality code. Avoid unnecessary complexity. Prefer readability over cleverness. Use strong typing where applicable. Handle failures explicitly. Never ignore exceptions. Use structured logging. Document public interfaces. Write self-explanatory code.

---

## Testing Requirements

Every implementation must include: unit tests · integration tests · regression tests (where applicable) · performance validation (when required). **All tests must pass before marking a sprint complete.**

---

## Documentation

Whenever implementation changes: update documentation · update API documentation · update architecture references if necessary · update `CHANGELOG.md`.

---

## Sprint Workflow

Every session must begin by reading: `CLAUDE.md` · `PROJECT_STATUS.md` · `implementation/CURRENT_SPRINT.md`. If necessary, also read the architecture chapters referenced by the current sprint. **Do not load unrelated architecture.**

**Before implementation**, understand: objective · dependencies · acceptance criteria · definition of done. Only then begin.

**During implementation:** modify only files required for the current sprint · avoid unrelated refactoring · avoid architecture drift · keep commits focused.

**After implementation:** run all required tests · verify acceptance criteria · update `CURRENT_SPRINT.md`, `DONE.md`, `BACKLOG.md`, `CHANGELOG.md`, `PROJECT_STATUS.md` · then **stop**. Do not automatically begin the next sprint.

---

## Architecture Change Policy

Never modify Volumes 1–7 directly. If a better design is discovered: document it as an ADR (Problem · Alternatives · Trade-offs · Recommendation) and wait for approval.

---

## Dependency Policy

Do not introduce new dependencies without justification. Every dependency must document: purpose · benefits · risks · maintenance impact · licensing considerations.

---

## Security

Never expose secrets. Never hardcode credentials. Never commit API keys. Validate all external inputs. Follow Volume 4 security architecture.

---

## Performance

Respect latency budgets defined by Volume 1. Avoid blocking operations on the critical path. Minimize unnecessary allocations. Prefer streaming where architecture specifies streaming.

---

## AI Model Rules

STT, LLM, and TTS are replaceable adapters. Never couple business logic to a specific model. Business behavior must remain model-agnostic.

---

## Git Workflow

One sprint = one logical change. Keep commits atomic. Write meaningful commit messages. Do not combine unrelated work.

---

## Communication Style

Be precise. Explain engineering trade-offs. State assumptions. Report blockers immediately. Never hide uncertainty.

---

## Success Criteria

The goal is not simply to write code. The goal is to build a production-grade VoiceOS implementation that faithfully follows the architecture, passes all acceptance criteria, remains maintainable, and is ready for enterprise deployment.

- When in doubt: **follow the architecture.**
- When architecture is ambiguous: **ask before implementing.**
- When architecture is clear: **implement confidently.**
