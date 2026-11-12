import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from plan_base_check import CheckError, check, validate

CLI = Path(__file__).with_name('plan_base_check.py')


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        self.run_git('init', '-q')
        self.run_git('config', 'user.name', 'Synthetic Tester')
        self.run_git('config', 'user.email', 'test@example.invalid')
        self.write('src/a.txt', 'base\n')
        self.write('docs/note.txt', 'note\n')
        self.run_git('add', '.')
        self.run_git('commit', '-qm', 'fixture')
        self.base = self.run_git('rev-parse', 'HEAD').strip()
        self.plan = {'base_commit': self.base, 'scope': ['src']}

    def run_git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True,
                                       stderr=subprocess.PIPE)

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def result(self):
        return check(self.repo, self.plan)

    def cli(self, value=None):
        planfile = Path(self.tmp.name) / 'plan.json'
        planfile.write_text(json.dumps(self.plan if value is None else value))
        result = subprocess.run([sys.executable, str(CLI), str(planfile), '--repo', str(self.repo)],
                                capture_output=True, text=True)
        return result.returncode, json.loads(result.stdout)

    def test_clean_cli_and_no_index_write(self):
        index = self.repo / '.git/index'
        before = (index.read_bytes(), index.stat().st_mtime_ns)
        self.assertEqual(self.cli()[0], 0)
        self.assertEqual(self.result()['status'], 'pass')
        self.assertEqual(before, (index.read_bytes(), index.stat().st_mtime_ns))

    def test_staged_unstaged_and_untracked(self):
        self.write('src/a.txt', 'staged\n')
        self.run_git('add', '.')
        self.write('src/a.txt', 'unstaged\n')
        self.write('src/new\nname.txt', 'new')
        changes = self.result()['changes']
        self.assertEqual(changes['staged'], ['src/a.txt'])
        self.assertEqual(changes['unstaged'], ['src/a.txt'])
        self.assertEqual(changes['untracked'], ['src/new\nname.txt'])
        self.assertEqual(self.cli()[0], 1)

    def test_outside_scope_dirt_is_not_stale(self):
        self.write('docs/note.txt', 'different')
        self.write('src-other/new', 'new')
        self.assertEqual(self.result()['status'], 'pass')

    def test_committed_scoped_drift(self):
        self.write('src/a.txt', 'changed')
        self.run_git('commit', '-qam', 'change')
        result = self.result()
        self.assertTrue(result['base_changed'])
        self.assertEqual(result['changes']['committed'], ['src/a.txt'])

    def test_stale_base_even_when_change_is_outside_scope(self):
        self.write('docs/note.txt', 'changed')
        self.run_git('commit', '-qam', 'change')
        result = self.result()
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['changes']['committed'], [])

    def test_deleted_scoped_file(self):
        (self.repo / 'src/a.txt').unlink()
        self.assertEqual(self.result()['changes']['unstaged'], ['src/a.txt'])

    def test_rename_out_of_scope_keeps_source_visible(self):
        self.run_git('mv', 'src/a.txt', 'docs/moved.txt')
        self.assertEqual(self.result()['changes']['staged'], ['src/a.txt'])

    def test_literal_pathspec_metacharacters(self):
        self.plan['scope'] = ['src/[a]']
        self.write('src/a', 'not in scope')
        self.write('src/[a]/new', 'in scope')
        self.assertEqual(self.result()['changes']['untracked'], ['src/[a]/new'])

    def test_missing_base_is_unknown_cli(self):
        self.plan['base_commit'] = '0' * 40
        code, result = self.cli()
        self.assertEqual((code, result['status']), (2, 'unknown'))

    def test_blob_is_not_commit(self):
        self.plan['base_commit'] = self.run_git('rev-parse', 'HEAD:src/a.txt').strip()
        with self.assertRaisesRegex(CheckError, 'commit object'):
            self.result()

    def test_invalid_plan_and_scope(self):
        for scope in [[], ['../x'], ['/tmp'], ['a/../b'], ['a//b'], ['.'], ['a/'], ['C:/x'], ['a\\b'], [4], ['a\0b']]:
            with self.subTest(scope=scope), self.assertRaises(CheckError):
                validate({'base_commit': self.base, 'scope': scope})
        for base in ['HEAD', '--help', 'abc', 'f' * 41]:
            with self.subTest(base=base), self.assertRaises(CheckError):
                validate({'base_commit': base, 'scope': ['src']})
        with self.assertRaises(CheckError):
            validate([])

    def test_subdirectory_rejected(self):
        with self.assertRaisesRegex(CheckError, 'root'):
            check(self.repo / 'src', self.plan)

    def test_git_environment_cannot_redirect_repo(self):
        with patch.dict(os.environ, {'GIT_DIR': '/missing', 'GIT_WORK_TREE': '/missing', 'GIT_INDEX_FILE': '/missing'}):
            self.assertEqual(self.result()['status'], 'pass')

    def test_external_diff_and_fsmonitor_not_run(self):
        sentinel = Path(self.tmp.name) / 'EXECUTED'
        script = Path(self.tmp.name) / 'unexpected.sh'
        script.write_text(f'#!/bin/sh\ntouch "{sentinel}"\nexit 1\n')
        script.chmod(0o755)
        self.run_git('config', 'diff.external', str(script))
        self.run_git('config', 'core.fsmonitor', str(script))
        self.write('src/a.txt', 'changed')
        self.assertEqual(self.result()['status'], 'stale')
        self.assertFalse(sentinel.exists())

    def test_process_filter_not_run(self):
        self.run_git('config', 'filter.marker.process', 'exit 1')
        with self.assertRaisesRegex(CheckError, 'filters prevent safe inspection'):
            self.result()

    def test_clean_filter_not_run(self):
        sentinel = Path(self.tmp.name) / 'FILTER_EXECUTED'
        script = Path(self.tmp.name) / 'filter.sh'
        script.write_text(f'#!/bin/sh\ntouch "{sentinel}"\ncat\n')
        script.chmod(0o755)
        self.run_git('config', 'filter.marker.clean', str(script))
        self.run_git('config', 'filter.marker.required', 'true')
        self.write('.gitattributes', 'src/* filter=marker\n')
        self.write('src/a.txt', 'changed')
        with self.assertRaisesRegex(CheckError, 'filters prevent safe inspection'):
            self.result()
        self.assertFalse(sentinel.exists())

    def test_unmerged_index_is_unknown(self):
        blob = self.run_git('rev-parse', 'HEAD:src/a.txt').strip()
        subprocess.run(['git', '-C', str(self.repo), 'update-index', '--index-info'],
                       input=f'0 {"0" * 40}\tsrc/a.txt\n100644 {blob} 1\tsrc/a.txt\n',
                       text=True, check=True, capture_output=True)
        with self.assertRaisesRegex(CheckError, 'unresolved merge'):
            self.result()

    def test_unborn_head_is_unknown(self):
        self.run_git('checkout', '--orphan', 'empty')
        with self.assertRaises(CheckError):
            self.result()

    def test_invalid_json_cli(self):
        bad = Path(self.tmp.name) / 'bad.json'
        bad.write_text('{')
        result = subprocess.run([sys.executable, str(CLI), str(bad), '--repo', str(self.repo)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'unknown')

    def test_non_repository_is_unknown(self):
        with self.assertRaises(CheckError):
            check(Path(self.tmp.name), self.plan)


if __name__ == '__main__':
    unittest.main()
