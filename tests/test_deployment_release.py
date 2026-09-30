"""Offline checks for the release package review and protected release paths."""
import io
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from deploy.package_release import describe_changes, validate_code_only_changes


class DeploymentPackageTests(unittest.TestCase):
    def archive(self, path, files):
        with tarfile.open(path, 'w:gz') as archive:
            for name, body in files.items():
                raw = body.encode()
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))

    def test_code_change_preserves_immutable_release_files(self):
        baseline = {'requirements.lock.txt': 'locked',
                    'dzmm_bot/persistence/migrations/versions/0001.py': 'history',
                    'dzmm_bot/core.py': 'old'}
        candidate = {**baseline, 'dzmm_bot/core.py': 'new', 'docs/new.md': 'guide'}
        with tempfile.TemporaryDirectory() as directory:
            old, new = Path(directory) / 'old.tar.gz', Path(directory) / 'new.tar.gz'
            self.archive(old, baseline)
            self.archive(new, candidate)
            with redirect_stdout(io.StringIO()):
                changes = describe_changes(old, new)
            validate_code_only_changes(changes)
            self.assertEqual(changes['changed'], ['dzmm_bot/core.py'])
            self.assertEqual(changes['added'], ['docs/new.md'])

    def test_unsafe_overlay_release_stops_for_manual_procedure(self):
        for changes in (
            {'added': [], 'changed': [], 'removed': ['dzmm_bot/old.py']},
            {'added': [], 'changed': ['requirements.txt'], 'removed': []},
            {'added': [], 'changed': ['requirements.lock.txt'], 'removed': []},
            {'added': [], 'changed': ['dzmm_bot/persistence/migrations/versions/0001.py'], 'removed': []},
            {'added': ['dzmm_bot/persistence/migrations/versions/9999.py'], 'changed': [], 'removed': []},
        ):
            with self.subTest(changes=changes), self.assertRaises(SystemExit):
                validate_code_only_changes(changes)


if __name__ == '__main__':
    unittest.main()
