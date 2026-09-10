"""Sales Intelligence Layer — Phase 2 of VoiceOS.

Adds a deterministic, domain-configurable qualification pipeline on top of
the existing Conversation Intelligence Layer (CIL). All engines in this
package are fast (no LLM calls, no network I/O).

Components:
  schema           — SalesState dataclass + all enums
  domains/base     — DomainConfig abstract base
  domains/real_estate — RealEstateDomainConfig (Delhi-NCR real estate)
  state_updater    — SalesStateUpdater: CIL outputs → SalesState
  question_selector — QuestionSelector: what field to ask next
  action_planner   — SalesActionPlanner: what action to take this turn
  post_call_summary — post_call_summary(): serialize SalesState to dict

Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
"""
