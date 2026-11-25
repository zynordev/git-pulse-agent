import tempfile
import unittest
from pathlib import Path

from git_pulse_agent.guard import is_protected_repo, scan_directory_for_secrets


class TestGuard(unittest.TestCase):
    def test_protected_repo_matching(self):
        patterns = ["2204a", "2204c", "research-*"]
        self.assertTrue(is_protected_repo("2204a-pqc-kronecker", "/tmp/2204a-pqc-kronecker", "", patterns))
        self.assertTrue(is_protected_repo("adelie-2204c", "/tmp/adelie-2204c", "", patterns))
        self.assertTrue(is_protected_repo("research-paper", "/tmp/research-paper", "", patterns))
        self.assertFalse(is_protected_repo("my-cli-tool", "/tmp/my-cli-tool", "https://github.com/user/my-cli-tool", patterns))

    def test_secret_scanner_detects_tokens_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "clean.py").write_text("print('hello world')\n", encoding="utf-8")
            self.assertEqual(scan_directory_for_secrets(root), [])

            (root / ".env").write_text("FOO=bar\n", encoding="utf-8")
            (root / "leak.py").write_text('TOKEN = "ntn_1234567890abcdefghijklmno"\n', encoding="utf-8")
            findings = scan_directory_for_secrets(root)
            rules = {f["rule"] for f in findings}
            self.assertIn("sensitive_filename", rules)
            self.assertIn("notion_token", rules)

    def test_secret_scanner_ignores_ancestor_skip_dirs_but_prunes_child_skip_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Project located under an ancestor directory named "build" (in SKIP_DIRS)
            project_root = Path(tmp) / "build" / "my-app"
            project_root.mkdir(parents=True)
            (project_root / ".env").write_text("SECRET=1\n", encoding="utf-8")

            # Child skip directory inside the project should still be ignored
            ignored_child = project_root / "node_modules" / "pkg"
            ignored_child.mkdir(parents=True)
            (ignored_child / "credentials.json").write_text("{}\n", encoding="utf-8")

            findings = scan_directory_for_secrets(project_root)
            self.assertEqual(findings, [{"file": ".env", "rule": "sensitive_filename"}])


if __name__ == "__main__":
    unittest.main()
