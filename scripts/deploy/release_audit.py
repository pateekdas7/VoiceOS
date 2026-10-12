#!/opt/voiceos/venv/bin/python3
"""
release_audit.py — record every deployment to the release_deployments table.

Usage:
    python release_audit.py record --env <staging|production> --tag <git-tag> \
        [--image <docker-image>] [--deployed-by <user>] [--notes <text>]

    python release_audit.py list [--env <env>] [--limit <N>]
    python release_audit.py get  --id <deployment-id>
"""
import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://voiceos:RJKft1c3q0mEBcMdUE0NQSHbYGw@127.0.0.1:5432/voiceos",
)


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS release_deployments (
                id              BIGSERIAL PRIMARY KEY,
                environment     TEXT        NOT NULL,
                git_tag         TEXT        NOT NULL,
                docker_image    TEXT,
                deployed_by     TEXT        NOT NULL DEFAULT 'ci',
                deployed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                git_sha         TEXT,
                git_branch      TEXT,
                hostname        TEXT,
                notes           TEXT,
                status          TEXT        NOT NULL DEFAULT 'started',
                finished_at     TIMESTAMPTZ,
                metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS idx_rd_env_deployed
                ON release_deployments (environment, deployed_at DESC);
            """
        )
    conn.commit()


def _git(cmd):
    try:
        return subprocess.check_output(
            ["git"] + cmd.split(), cwd="/opt/voiceos/app", stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return None


def cmd_record(args):
    git_sha = _git("rev-parse HEAD")
    git_branch = _git("rev-parse --abbrev-ref HEAD")

    conn = get_conn()
    ensure_table(conn)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO release_deployments
                (environment, git_tag, docker_image, deployed_by,
                 git_sha, git_branch, hostname, notes, status, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'started', %s)
            RETURNING id, deployed_at
            """,
            (
                args.env,
                args.tag,
                args.image,
                args.deployed_by,
                git_sha,
                git_branch,
                socket.gethostname(),
                args.notes,
                json.dumps({"cli_args": sys.argv[1:]}),
            ),
        )
        row = cur.fetchone()
    conn.commit()
    conn.close()

    print(json.dumps({"id": row["id"], "deployed_at": row["deployed_at"].isoformat()}))


def cmd_finish(args):
    conn = get_conn()
    ensure_table(conn)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE release_deployments
               SET status = %s, finished_at = NOW()
             WHERE id = %s
            RETURNING id, status, finished_at
            """,
            (args.status, args.id),
        )
        row = cur.fetchone()
    conn.commit()
    conn.close()

    if row is None:
        print(json.dumps({"error": f"No deployment with id={args.id}"}))
        sys.exit(1)
    print(json.dumps({"id": row["id"], "status": row["status"], "finished_at": row["finished_at"].isoformat()}))


def cmd_list(args):
    conn = get_conn()
    ensure_table(conn)

    where = "WHERE environment = %s" if args.env else ""
    params = [args.env] if args.env else []
    params.append(args.limit)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id, environment, git_tag, docker_image, deployed_by,
                   deployed_at, git_sha, status, notes
              FROM release_deployments
              {where}
             ORDER BY deployed_at DESC
             LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
    conn.close()

    for r in rows:
        r["deployed_at"] = r["deployed_at"].isoformat()
    print(json.dumps(rows, indent=2))


def cmd_get(args):
    conn = get_conn()
    ensure_table(conn)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM release_deployments WHERE id = %s", (args.id,))
        row = cur.fetchone()
    conn.close()

    if row is None:
        print(json.dumps({"error": f"No deployment with id={args.id}"}))
        sys.exit(1)
    row["deployed_at"] = row["deployed_at"].isoformat()
    if row["finished_at"]:
        row["finished_at"] = row["finished_at"].isoformat()
    row["metadata"] = dict(row["metadata"])
    print(json.dumps(row, indent=2))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record")
    rec.add_argument("--env", required=True, choices=["staging", "production"])
    rec.add_argument("--tag", required=True)
    rec.add_argument("--image", default=None)
    rec.add_argument("--deployed-by", default=os.environ.get("DEPLOY_USER", "ci"))
    rec.add_argument("--notes", default=None)

    fin = sub.add_parser("finish")
    fin.add_argument("--id", type=int, required=True)
    fin.add_argument("--status", choices=["success", "failed", "rolled_back"], default="success")

    lst = sub.add_parser("list")
    lst.add_argument("--env", default=None)
    lst.add_argument("--limit", type=int, default=20)

    get = sub.add_parser("get")
    get.add_argument("--id", type=int, required=True)

    args = p.parse_args()

    dispatch = {
        "record": cmd_record,
        "finish": cmd_finish,
        "list":   cmd_list,
        "get":    cmd_get,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
