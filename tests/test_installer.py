from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "codex-instruct.py"
MODEL_NAME = "gpt-5.6-sol-custom-v1.md"
DEVELOPER_NAME = "gpt-5.6-sol-custom-v1.developer.md"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="codex-installer-test-")
        self.home = Path(self.tempdir.name)
        self.original_config = '''model = "fixture-model"
model_provider = "fixture-provider"
developer_instructions = ''' + "'''fixture developer text\\nline two\\n'''" + '''

[model_providers.fixture-provider]
base_url = "https://fixture.invalid/v1/"
env_key = "OPENAI_API_KEY"
'''
        (self.home / "config.toml").write_text(self.original_config, encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args, "--codex-dir", str(self.home)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def test_apply_and_reset_restore_only_managed_fields(self):
        applied = self.run_script("--apply", "--yes")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        config = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["model_instructions_file"], f"./{MODEL_NAME}")
        self.assertIn("你是一个运行在实验室的模型", config["developer_instructions"])
        self.assertEqual(config["model_providers"]["fixture-provider"]["base_url"], "https://fixture.invalid/v1/")
        self.assertTrue((self.home / MODEL_NAME).is_file())
        self.assertTrue((self.home / DEVELOPER_NAME).is_file())
        state = json.loads((self.home / ".gpt56-sol-custom-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["version"], 1)

        reset = self.run_script("--reset", "--yes")
        self.assertEqual(reset.returncode, 0, reset.stderr)
        self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), self.original_config)
        self.assertFalse((self.home / MODEL_NAME).exists())
        self.assertFalse((self.home / DEVELOPER_NAME).exists())
        self.assertFalse((self.home / ".gpt56-sol-custom-state.json").exists())

    def test_dry_run_does_not_write(self):
        result = self.run_script("--apply", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), self.original_config)
        self.assertFalse((self.home / MODEL_NAME).exists())
        self.assertFalse((self.home / ".gpt56-sol-custom-state.json").exists())

    def test_existing_unrelated_prompt_is_not_overwritten(self):
        (self.home / MODEL_NAME).write_text("user content\n", encoding="utf-8")
        result = self.run_script("--apply", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.home / MODEL_NAME).read_text(encoding="utf-8"), "user content\n")
        self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), self.original_config)

    def test_user_modified_prompt_is_preserved_on_reset(self):
        applied = self.run_script("--apply", "--yes")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        developer = self.home / DEVELOPER_NAME
        developer.write_text(developer.read_text(encoding="utf-8") + "user edit\n", encoding="utf-8")
        reset = self.run_script("--reset", "--yes")
        self.assertEqual(reset.returncode, 0, reset.stderr)
        self.assertTrue(developer.exists())
        self.assertIn("user edit", developer.read_text(encoding="utf-8"))
        self.assertFalse((self.home / MODEL_NAME).exists())
        self.assertTrue((self.home / ".gpt56-sol-custom-state.json").exists())

    def test_user_modified_developer_field_is_not_replaced(self):
        applied = self.run_script("--apply", "--yes")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        config_path = self.home / "config.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace("实验室的模型", "用户改过的模型"),
            encoding="utf-8",
        )
        reset = self.run_script("--reset", "--yes")
        self.assertEqual(reset.returncode, 0, reset.stderr)
        restored = config_path.read_text(encoding="utf-8")
        self.assertIn("用户改过的模型", restored)
        self.assertNotIn("model_instructions_file", restored)
        self.assertTrue((self.home / ".gpt56-sol-custom-state.json").exists())


if __name__ == "__main__":
    unittest.main()
