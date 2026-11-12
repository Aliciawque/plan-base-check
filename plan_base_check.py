#!/usr/bin/env python3
"""Read-only Git freshness checks for a JSON implementation-plan handoff."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


class CheckError(Exception):
    pass


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0',
               GIT_NO_LAZY_FETCH='1')
    try:
        result = subprocess.run(
            ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
             '-C', str(repo), *args], env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckError(f'Git could not run: {exc}') from exc
    if result.returncode:
        raise CheckError('Git check failed: ' + result.stderr.decode('utf-8', 'replace').strip())
    return result.stdout


def validate(plan):
    if not isinstance(plan, dict):
        raise CheckError('Plan must be a JSON object')
    base, scope = plan.get('base_commit'), plan.get('scope')
    if not isinstance(base, str) or not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', base):
        raise CheckError('base_commit must be a full 40- or 64-character hexadecimal commit ID')
    if not isinstance(scope, list) or not scope:
        raise CheckError('scope must be a nonempty list of repository-relative literal paths')
    for item in scope:
        if (not isinstance(item, str) or not item or item.startswith('/')
                or '\\' in item or ':' in item or any(ord(c) < 32 for c in item)
                or any(p in ('', '.', '..') for p in item.split('/'))):
            raise CheckError('Invalid scope: use relative paths without empty, dot, or parent components')
    return base.lower(), sorted(set(scope))


def check(repo, plan):
    base, scope = validate(plan)
    root = git(repo, 'rev-parse', '--show-toplevel').decode().strip()
    if Path(root).resolve() != Path(repo).resolve():
        raise CheckError('Repository must be its working-tree root (scope is root-relative)')
    head = git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
    # Requiring an actual commit avoids accepting a blob or tag object as a plan base.
    if git(repo, 'cat-file', '-t', base).strip() != b'commit':
        raise CheckError('base_commit does not identify a commit object')

    def scoped(data):
        paths = [os.fsdecode(p) for p in data.split(b'\0') if p]
        return sorted({p for p in paths if any(p == s or p.startswith(s + '/') for s in scope)})

    if git(repo, 'ls-files', '--unmerged', '-z'):
        raise CheckError('Index contains unresolved merge conflicts; resolve before checking a plan')

    # Git can run clean/process filters even with external diff/textconv off.
    # Refuse rather than disable normalization and risk a misleading result.
    config_keys = git(repo, 'config', '--null', '--name-only', '--list').split(b'\0')
    if any(k.startswith(b'filter.') and k.endswith((b'.clean', b'.process')) for k in config_keys):
        raise CheckError('Configured clean/process filters prevent safe inspection; no filters were run')

    diff = ('diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--ignore-submodules=none', '--name-only', '-z')
    changes = {
        'committed': scoped(git(repo, *diff, base, head, '--')),
        'staged': scoped(git(repo, *diff, '--cached', head, '--')),
        'unstaged': scoped(git(repo, *diff, '--')),
        'untracked': scoped(git(repo, 'ls-files', '--others', '--exclude-standard', '-z')),
    }
    # Detect HEAD changes during the multi-command observation rather than report a false pass.
    if git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip() != head:
        raise CheckError('HEAD changed during inspection; retry')
    stale = base != head
    return {'status': 'stale' if stale or any(changes.values()) else 'pass',
            'base_commit': base, 'head_commit': head, 'base_changed': stale,
            'scope': scope, 'changes': changes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path, help='JSON with base_commit and scope')
    parser.add_argument('--repo', type=Path, default=Path.cwd(), help='working-tree root')
    args = parser.parse_args()
    try:
        result = check(args.repo, json.loads(args.plan.read_text(encoding='utf-8')))
    except (CheckError, OSError, ValueError) as exc:
        result = {'status': 'unknown', 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return {'pass': 0, 'stale': 1, 'unknown': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
