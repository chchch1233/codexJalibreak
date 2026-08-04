from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from benchmarks.labs import (
    PENTEST_SECRET,
    REVERSE_TOKEN,
    build_pentest_fixture,
    build_reverse_fixture,
    injection_proves_secret,
    start_pentest_server,
    verify_reverse_token,
)


class LabTests(unittest.TestCase):
    def test_reverse_fixture_verifies_known_token(self):
        with tempfile.TemporaryDirectory(prefix="reverse-lab-test-") as directory:
            fixture = build_reverse_fixture(Path(directory))
            self.assertTrue(verify_reverse_token(Path(str(fixture["binary"])), REVERSE_TOKEN))

    def test_pentest_fixture_exposes_only_local_injection_signal(self):
        with tempfile.TemporaryDirectory(prefix="pentest-lab-test-") as directory:
            fixture = build_pentest_fixture(Path(directory))
            server = start_pentest_server(fixture)
            try:
                self.assertEqual(server.base_url.split("//", 1)[1].split(":", 1)[0], "127.0.0.1")
                self.assertTrue(injection_proves_secret(server.base_url, PENTEST_SECRET))
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
