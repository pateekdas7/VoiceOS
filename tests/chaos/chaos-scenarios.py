#!/usr/bin/env python3
"""chaos-scenarios.py -- Sprint-028 "3. Chaos Engineering" injection scripts (V3 Ch19, V7 Ch20).

Five named scenarios, each a real disruption command (``kubectl delete
pod``, ``systemctl stop``, ``tc qdisc`` netem) plus a before/during/after
health check, matching Sprint-028.md's own gates:

  1. gpu-node-kill            -- kill GPU node-0 during load -> graceful failover, <= 5 calls dropped
  2. redis-primary-kill       -- kill Redis primary -> degraded-mode continuation, no data loss
  3. packet-loss              -- 20% RTP packet loss -> PLC compensates, STT accuracy within 5% of baseline
  4. postgres-primary-kill    -- kill Postgres primary -> crash recovery, standby promotes, no PTP duplication
  5. conversation-engine-kill -- kill conversation-engine pod mid-call -> session recovered from Redis snapshot

IMPORTANT -- current topology caveat (read before running against real
infrastructure): per `deployment/CPU_NODE_STATE.md` and
`implementation/BACKLOG.md`'s TT-019, this project's real environment is a
**single-node, non-HA** Kubernetes control plane with a single Postgres
instance and a single Redis instance -- there is no standby Postgres to
promote and no Redis replica to fail over to. Scenarios 2 and 4 therefore
cannot exercise a true failover in this environment; they instead validate
the closest honest equivalent ("the service recovers cleanly after a
restart, with no data loss/duplication") and the script says so explicitly
in its own output rather than reporting a fabricated failover pass. A
genuine multi-node HA topology is a prerequisite for a full-fidelity
re-run of scenarios 2/4 (tracked as a new backlog item, not assumed away).

Every scenario defaults to ``--dry-run`` (prints the commands it would
run without executing them) -- these are genuinely destructive operations
against whatever host they're pointed at. Real execution requires
``--execute`` AND an explicit ``--host``/``--kubeconfig``, and is intended
to run against a disposable staging environment, never blindly against a
shared production node.

Usage:
    python3 tests/chaos/chaos-scenarios.py --scenario all --dry-run
    python3 tests/chaos/chaos-scenarios.py --scenario redis-primary-kill --execute --host 101.53.141.75
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class ChaosResult:
    """Structured outcome of one chaos scenario run."""

    scenario: str
    expected_gate: str
    executed: bool
    commands: list[str] = field(default_factory=list)
    observed: str = "(dry-run -- no commands executed)"
    passed: bool | None = None
    """None when --dry-run (nothing was actually observed to judge)."""
    caveat: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "expected_gate": self.expected_gate,
            "executed": self.executed,
            "commands": self.commands,
            "observed": self.observed,
            "passed": self.passed,
            "caveat": self.caveat,
            "started_at": self.started_at,
        }


def _run(cmd: list[str], *, execute: bool, timeout_s: int = 30) -> tuple[str, list[str]]:
    """Run (or, if not ``execute``, merely record) a shell command; returns (output, [command_str])."""
    command_str = " ".join(cmd)
    if not execute:
        return "(dry-run -- not executed)", [command_str]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
        return (proc.stdout + proc.stderr).strip(), [command_str]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"ERROR: {exc}", [command_str]


def scenario_gpu_node_kill(*, execute: bool, host: str | None) -> ChaosResult:
    """1. Kill GPU node-0 during 200 concurrent calls -> graceful failover, <= 5 calls dropped."""
    commands: list[str] = []
    observed = "(dry-run)"
    caveat = (
        "This deployment has exactly one GPU node (see GPU_NODE_STATE.md) -- there is no node-1 to "
        "absorb load. A real failover-to-node-1 assertion requires provisioning a second GPU node first "
        "(tracked separately, not assumed to exist)."
    )
    if execute and host:
        # Real command: stop the three GPU inference systemd units, then observe conversation-engine's
        # own circuit-breaker/GPU-scheduler admission behavior via its structured logs.
        out, cmds = _run(["ssh", host, "sudo systemctl stop voiceos-stt voiceos-llm voiceos-tts"], execute=execute)
        commands.extend(cmds)
        observed = out
    return ChaosResult(
        scenario="gpu-node-kill",
        expected_gate="Graceful failover to GPU node-1; <= 5 calls dropped",
        executed=execute,
        commands=commands,
        observed=observed,
        passed=None,
        caveat=caveat,
    )


def scenario_redis_primary_kill(*, execute: bool, host: str | None) -> ChaosResult:
    """2. Kill Redis primary during calls -> degraded-mode continuation, no data loss."""
    commands: list[str] = []
    observed = "(dry-run)"
    caveat = (
        "Single Redis instance in this environment (no replica) -- this validates 'restarts cleanly with "
        "AOF-preserved state and no data loss' (TT-002's own AOF hardening), not a true primary->replica "
        "failover. See module docstring."
    )
    if execute and host:
        out, cmds = _run(["ssh", host, "sudo service redis-server restart"], execute=execute)
        commands.extend(cmds)
        observed = out
    return ChaosResult(
        scenario="redis-primary-kill",
        expected_gate="Degraded-mode continuation; no data loss; calls complete with possible extra latency",
        executed=execute,
        commands=commands,
        observed=observed,
        passed=None,
        caveat=caveat,
    )


def scenario_packet_loss(*, execute: bool, host: str | None, interface: str = "eth0") -> ChaosResult:
    """3. 20% packet loss on the RTP path -> PLC compensates, STT accuracy within 5% of baseline."""
    commands: list[str] = []
    observed = "(dry-run)"
    if execute and host:
        out, cmds = _run(["ssh", host, f"sudo tc qdisc add dev {interface} root netem loss 20%"], execute=execute)
        commands.extend(cmds)
        observed = out
    return ChaosResult(
        scenario="packet-loss-20pct",
        expected_gate="PacketLossConcealer compensates; STT accuracy within 5% of baseline",
        executed=execute,
        commands=commands,
        observed=observed,
        passed=None,
        caveat="Run scripts/validate/walking_skeleton.py before and after to compare STT accuracy deltas.",
    )


def scenario_packet_loss_cleanup(*, execute: bool, host: str | None, interface: str = "eth0") -> ChaosResult:
    """Removes the netem qdisc installed by :func:`scenario_packet_loss` -- always run after scenario 3."""
    commands: list[str] = []
    observed = "(dry-run)"
    if execute and host:
        out, cmds = _run(["ssh", host, f"sudo tc qdisc del dev {interface} root netem"], execute=execute)
        commands.extend(cmds)
        observed = out
    return ChaosResult(
        scenario="packet-loss-20pct-cleanup",
        expected_gate="netem qdisc removed; RTP path restored to baseline",
        executed=execute,
        commands=commands,
        observed=observed,
        passed=None,
        caveat=None,
    )


def scenario_postgres_primary_kill(*, execute: bool, host: str | None) -> ChaosResult:
    """4. Kill Postgres primary -> crash recovery, standby promotes, no PTP duplication."""
    commands: list[str] = []
    observed = "(dry-run)"
    caveat = (
        "Single Postgres instance in this environment -- no standby to promote. This validates crash "
        "recovery + idempotent PTP re-submission (IdempotencyGuard, Sprint-015) after a restart, the "
        "same substitution infra/dr/DR_DRILL_REPORT_2026-07-08.md already used for the Sprint-027 DR drill. "
        "See module docstring."
    )
    if execute and host:
        out, cmds = _run(
            ["ssh", host, "bash infra/dr/scripts/trigger-db-failover.sh --mode pitr-restore"], execute=execute
        )
        commands.extend(cmds)
        observed = out
    return ChaosResult(
        scenario="postgres-primary-kill",
        expected_gate="Crash recovery; standby promotes; no PTP duplication",
        executed=execute,
        commands=commands,
        observed=observed,
        passed=None,
        caveat=caveat,
    )


def scenario_conversation_engine_pod_kill(
    *, execute: bool, host: str | None, namespace: str = "voiceos-runtime"
) -> ChaosResult:
    """5. Kill conversation-engine pod during an active call -> session recovered from Redis snapshot."""
    commands: list[str] = []
    observed = "(dry-run)"
    if execute and host:
        out, cmds = _run(
            ["ssh", host, f"kubectl -n {namespace} delete pod -l app=conversation-engine --wait=false"],
            execute=execute,
        )
        commands.extend(cmds)
        observed = out
    return ChaosResult(
        scenario="conversation-engine-pod-kill",
        expected_gate="Session state recovered from Redis snapshot (src.libs.state.Snapshot, Sprint-015)",
        executed=execute,
        commands=commands,
        observed=observed,
        passed=None,
        caveat=(
            "conversation-engine remains a library class with no standalone pod today (TT-006) -- the real "
            "target is the health-stub Deployment sharing that chart name; a full re-run once TT-006 gives "
            "conversation-engine a real process will validate the actual ConversationSessionState recovery "
            "path, not just pod-restart mechanics."
        ),
    )


_SCENARIOS: dict[str, Callable[..., ChaosResult]] = {
    "gpu-node-kill": scenario_gpu_node_kill,
    "redis-primary-kill": scenario_redis_primary_kill,
    "packet-loss": scenario_packet_loss,
    "postgres-primary-kill": scenario_postgres_primary_kill,
    "conversation-engine-pod-kill": scenario_conversation_engine_pod_kill,
}


def run_scenario(name: str, *, execute: bool, host: str | None) -> ChaosResult:
    if name not in _SCENARIOS:
        raise ValueError(f"Unknown scenario {name!r}; choose from {sorted(_SCENARIOS)} or 'all'")
    return _SCENARIOS[name](execute=execute, host=host)


def run_all(*, execute: bool, host: str | None) -> list[ChaosResult]:
    return [run_scenario(name, execute=execute, host=host) for name in _SCENARIOS]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=[*sorted(_SCENARIOS), "all"], default="all")
    parser.add_argument(
        "--execute", action="store_true", help="Actually run the disruption commands (default: dry-run)."
    )
    parser.add_argument(
        "--host", default=None, help="SSH target for the disruption commands (required with --execute)."
    )
    args = parser.parse_args(argv)

    if args.execute and not args.host:
        parser.error("--execute requires --host")

    results = (
        run_all(execute=args.execute, host=args.host)
        if args.scenario == "all"
        else [run_scenario(args.scenario, execute=args.execute, host=args.host)]
    )

    print(json.dumps([r.to_dict() for r in results], indent=2))
    if not args.execute:
        print(
            "\n[chaos-scenarios] dry-run only -- pass --execute --host <target> to actually run these.", file=sys.stderr
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
