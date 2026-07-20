from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]


class WebUIVersionStampTest(unittest.TestCase):
    def test_web_ui_build_stamps_git_version_and_commit(self):
        script = (REPO / "main/http_server/axe-os/scripts/build-versioned.sh").read_text()
        self.assertIn("if REPO_ROOT=\"$(git -C \"$WEB_DIR\" rev-parse --show-toplevel", script)
        self.assertNotIn("rev-parse --show-toplevel 2>/dev/null || cd", script)
        self.assertIn("git -C \"$REPO_ROOT\" rev-parse --short HEAD", script)
        self.assertIn('VERSION_TAG="dev-${COMMIT_HASH}"', script)
        self.assertIn('VERSION_TAG="${VERSION_TAG}-dirty"', script)
        self.assertNotIn("describe --tags --abbrev=0", script)
        self.assertIn('__VERSION__", version', script)
        self.assertIn('__COMMIT__", commit', script)
        self.assertIn("Web UI stamped with VERSION_TAG", script)

    def test_npm_build_uses_versioned_wrapper(self):
        package_json = (REPO / "main/http_server/axe-os/package.json").read_text()
        self.assertIn('"build": "bash scripts/build-versioned.sh"', package_json)

    def test_cmake_rebuilds_web_ui_dist(self):
        cmake = (REPO / "main/CMakeLists.txt").read_text()
        self.assertIn("BUILD_ALWAYS TRUE", cmake)

    def test_github_workflow_uses_same_wrapper_not_manual_sed(self):
        workflow = (REPO / ".github/workflows/build.yml").read_text()
        self.assertNotIn("sed -e", workflow)
        self.assertNotIn("mv \"./main/http_server/axe-os/src/assets/i18n", workflow)


if __name__ == "__main__":
    unittest.main()
