# plan-base-check

A small, read-only freshness check before executing an implementation plan. It compares a plan's recorded Git commit and literal file/directory scope with a local checkout, emitting JSON and a useful exit status. Python standard library only; no model, API key, installation, or network access required.

## Why

Plans can become stale between an audit and implementation. A clean worktree alone does not prove that a plan still targets the current commit, while a commit comparison alone misses staged, unstaged, and new files. This tool exposes both, without running a plan's instructions.

Inspired by the public plan handoff workflow in [shadcn/improve](https://github.com/shadcn/improve), not affiliated with it. No upstream code or plans are copied. Its plans are Markdown; this tool uses a separate JSON sidecar, not an automatic parser or official integration.

## Usage

Requires Python 3.10+ and Git. Tested on Linux with Python 3.12.14 and Git 2.52.0.

Record the full commit ID with `git rev-parse HEAD`, and create a sidecar outside the scoped files (or commit the plan separately and use that new HEAD):

```json
{
  "base_commit": "0123456789abcdef0123456789abcdef01234567",
  "scope": ["src/payments", "tests/test_payments.py"]
}
```

The hash above is a placeholder; use a real commit from your repository.

```sh
python3 plan_base_check.py /path/to/plan.json --repo /path/to/repository
```

`--repo` must be the working-tree root, not a subdirectory. Paths in `scope` are case-sensitive literal repository-relative paths, with `/` separators. Directories include all descendants; `src` does not match `src-other`. No globs or Git pathspec interpretation. A nonexistent path is allowed for a planned new file, but is not proof that the scope was spelled correctly. Whole-tree `.` scope is deliberately unsupported; list relevant paths.

### Results

- **Exit 0, `pass`:** HEAD equals the recorded base and no scoped committed, staged, unstaged, or untracked changes were observed.
- **Exit 1, `stale`:** HEAD differs (even if only out-of-scope files changed), or at least one scoped change was observed. Review and refresh the plan; the tool does not approve implementation.
- **Exit 2, `unknown`:** invalid input, missing Git/base commit, nonrepository/unborn checkout, unresolved merge index, configured clean/process filters, timeout, or changing HEAD prevents a reliable check. Do not treat this as a pass.

Output contains `base_changed`, both commit IDs, normalized scope, and four filename lists under `changes`: `committed`, `staged`, `unstaged`, `untracked`. Renames are represented as deletion/addition so a source moved outside the scope remains visible. Filenames are JSON-escaped, including newlines. Invalid CLI syntax also exits 2 via argparse, with usage text rather than JSON.

## Boundaries and safety

- Only inspection commands run. No checkout, fetch, commit, hook installation, repository configuration changes, or file writes. Git optional index refresh locks and lazy fetching are disabled.
- Configured clean/process filters (including global Git LFS filters) produce `unknown` before working-tree inspection, even if unused by scope. Disabling normalization could falsely classify content, so the tool refuses rather than executes or silently bypasses these filters.
- No shell interpolation. Base revisions must be full 40/64-character hexadecimal commit IDs; branch names and revision options are rejected. Absolute paths, parent/dot components, backslashes, colons and control characters in scope are rejected.
- Git environment overrides are cleared, replacement objects disabled, fsmonitor/hooks disabled, and external diff/textconv disabled. It uses the installed `git` executable and does not sandbox a compromised Git installation or arbitrary system configuration.
- Ignored untracked files are excluded. Dirty state outside scope is intentionally ignored, but *any* HEAD mismatch is stale. Sparse checkout/assume-unchanged/skip-worktree rules can hide filesystem edits from Git; this is not a filesystem integrity scanner.
- Submodule entries may be reported by Git, but their internal paths are not recursively inspected. Scope the submodule entry itself or run the tool separately in its working-tree root.
- Base-versus-HEAD is a tree comparison, **not an ancestry/divergence check**. A missing base in a shallow clone is unknown; no fetch is attempted.
- HEAD is rechecked after observation, but the index/worktree is **not an atomic snapshot**. Avoid concurrent edits and rerun immediately before execution. Passing does not establish semantic correctness, complete scope, safety, authorization, or that tests passed.

## Tests

```sh
python3 -m unittest -v
```

Tests create disposable synthetic Git repositories. They check CLI exit codes/JSON; scoped committed, staged, unstaged and untracked changes; deletions/renames; unusual filenames; invalid revisions/scope; environment isolation; unresolved merges; no index mutation; and that external diff/fsmonitor programs are not executed and configured clean/process filters are refused. No private repositories or credentials are used.

## Related tools and alternatives

This is a convenience wrapper, not a novel Git primitive. [`git diff`](https://git-scm.com/docs/git-diff) and [`git ls-files`](https://git-scm.com/docs/git-ls-files) can perform the underlying checks directly. [pre-commit](https://github.com/pre-commit/pre-commit) manages executable hooks, a broader and different workflow; this checker neither installs nor runs hooks. [improve](https://github.com/shadcn/improve) supports plan review/reconciliation, including semantic judgments this tool cannot make. Public search did not establish that no equivalent tool exists, and no uniqueness claim is made.

## License

MIT. See [LICENSE](LICENSE).
