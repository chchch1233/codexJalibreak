from __future__ import annotations

import subprocess
import sys
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "sync-archives.py"
ARCHIVE = ROOT / "gpt-5.6-sol-custom-v1.zip"


class ArchiveTests(unittest.TestCase):
    def test_archive_contains_only_current_prompt_pair(self):
        created = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(created.returncode, 0, created.stderr)
        checked = subprocess.run([sys.executable, str(SCRIPT), "--check"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        with zipfile.ZipFile(ARCHIVE) as archive:
            self.assertEqual(
                archive.namelist(),
                ["gpt-5.6-sol-custom-v1.md", "gpt-5.6-sol-custom-v1.developer.md"],
            )
            for name in archive.namelist():
                self.assertEqual(
                    archive.read(name),
                    (ROOT / "prompts" / name).read_bytes(),
                )


if __name__ == "__main__":
    unittest.main()
