#!/usr/bin/env python3
"""
W10 Key Rotation Script — VoiceOS
Rotates: JWT signing secret, BFF internal token, GPU secret.
Old values are backed up with a timestamp before replacement.
Run: python scripts/rotate_keys.py [--dry-run] [--target jwt|bff_token|gpu_secret|all]
"""
import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ENV_FILE = Path('/opt/voiceos/.env')
BACKUP_DIR = Path('/opt/voiceos/key_rotation_backups')

ROTATABLE_KEYS = {
    'jwt': 'JWT_SECRET',
    'bff_token': 'BFF_INTERNAL_TOKEN',
    'gpu_secret': 'GPU_SECRET',
}


def backup_env(dry_run: bool) -> Path:
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_path = BACKUP_DIR / f'.env.{ts}'
    if dry_run:
        print(f'[dry-run] Would backup {ENV_FILE} → {backup_path}')
        return backup_path
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    shutil.copy2(ENV_FILE, backup_path)
    os.chmod(backup_path, 0o600)
    print(f'✓ Backed up .env → {backup_path}')
    return backup_path


def generate_secret() -> str:
    return secrets.token_hex(32)


def rotate_key(env_content: str, var_name: str, dry_run: bool) -> tuple[str, str]:
    new_val = generate_secret()
    pattern = rf'^({re.escape(var_name)}=)(.+)$'
    match = re.search(pattern, env_content, re.MULTILINE)
    if not match:
        # Key not present — append it
        print(f'  {var_name} not found in .env — appending')
        new_content = env_content.rstrip('\n') + f'\n{var_name}={new_val}\n'
    else:
        old_val = match.group(2)
        print(f'  {var_name}: {old_val[:8]}… → {new_val[:8]}…')
        new_content = re.sub(pattern, rf'\g<1>{new_val}', env_content, flags=re.MULTILINE)
    return new_content, new_val


def restart_services(dry_run: bool) -> None:
    services = ['bff', 'webapi', 'voice-runtime', 'dialer-worker']
    for svc in services:
        unit = f'voiceos-{svc}.service'
        cmd = ['sudo', 'systemctl', 'restart', unit]
        if dry_run:
            print(f'[dry-run] Would restart: {" ".join(cmd)}')
        else:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                print(f'✓ Restarted {unit}')
            else:
                print(f'⚠ Could not restart {unit}: {result.stderr.strip()}')


def main() -> None:
    parser = argparse.ArgumentParser(description='VoiceOS W10 key rotation')
    parser.add_argument('--dry-run', action='store_true', help='Show what would change without writing')
    parser.add_argument('--target', default='all', choices=[*ROTATABLE_KEYS, 'all'],
                        help='Which key(s) to rotate (default: all)')
    parser.add_argument('--no-restart', action='store_true', help='Skip service restart after rotation')
    args = parser.parse_args()

    if not ENV_FILE.exists():
        print(f'ERROR: .env not found at {ENV_FILE}', file=sys.stderr)
        sys.exit(1)

    env_content = ENV_FILE.read_text()
    backup_env(args.dry_run)

    targets = list(ROTATABLE_KEYS.keys()) if args.target == 'all' else [args.target]
    new_secrets: dict[str, str] = {}

    print(f'\nRotating: {", ".join(targets)}\n')
    for target in targets:
        var_name = ROTATABLE_KEYS[target]
        env_content, new_val = rotate_key(env_content, var_name, args.dry_run)
        new_secrets[var_name] = new_val

    if not args.dry_run:
        ENV_FILE.write_text(env_content)
        os.chmod(ENV_FILE, 0o640)
        print(f'\n✓ Written {ENV_FILE}')

        if not args.no_restart:
            print('\nRestarting services…')
            restart_services(dry_run=False)

    print('\n✓ Key rotation complete')
    if args.dry_run:
        print('  (dry-run — no files changed, no services restarted)')


if __name__ == '__main__':
    main()
