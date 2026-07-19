#!/usr/bin/env python3
"""Static checks for reproducible firmware/Web UI version stamping."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BuildVersionStampTests(unittest.TestCase):
    def test_explicit_version_is_set_before_esp_idf_project_setup(self):
        cmake = (ROOT / "CMakeLists.txt").read_text()
        version_pos = cmake.index('set(PROJECT_VER "$ENV{VERSION_TAG}")')
        idf_setup_pos = cmake.index("include($ENV{IDF_PATH}/tools/cmake/project.cmake)")

        self.assertLess(version_pos, idf_setup_pos)

    def test_docker_wrappers_forward_host_version_and_commit(self):
        for relative_path in ("docker/idf.sh", "docker/idf-shell.sh"):
            script = (ROOT / relative_path).read_text()
            with self.subTest(script=relative_path):
                self.assertIn('git -C "$repo_dir" describe', script)
                self.assertIn('-e VERSION_TAG="$version_tag"', script)
                self.assertIn('-e COMMIT_HASH="$commit_hash"', script)

    def test_versioned_web_build_has_safe_non_git_fallbacks(self):
        script = (ROOT / "main/http_server/axe-os/scripts/build-versioned.sh").read_text()

        self.assertIn('VERSION_TAG="${VERSION_TAG:-local}"', script)
        self.assertIn('COMMIT_HASH="${COMMIT_HASH:-local}"', script)

    def test_release_workflow_forwards_version_to_the_container(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()

        self.assertIn('echo "COMMIT_HASH=${commit_hash}" >> "$GITHUB_ENV"', workflow)
        self.assertGreaterEqual(workflow.count("-e VERSION_TAG -e COMMIT_HASH"), 2)

    def test_release_workflow_cannot_publish_a_branch_as_an_old_tag(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()

        self.assertIn('if [ "$GITHUB_REF_TYPE" = "tag" ]', workflow)
        self.assertIn('version="dev-${commit_hash}"', workflow)
        self.assertIn('IS_TAG_BUILD=false', workflow)
        self.assertNotIn('version=$(git describe --tags --abbrev=0', workflow)

    def test_release_assets_use_newline_globs_and_must_exist(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()

        release_step = workflow.split("- name: Upload to Existing Release", 1)[1]
        release_step = release_step.split("- name: Install rclone", 1)[0]
        self.assertIn("files: |", release_step)
        self.assertIn("esp-miner-factory-${{ matrix.label }}-${{ env.VERSION_TAG }}.bin", release_step)
        self.assertIn("build/esp-miner-${{ matrix.label }}.bin", release_step)
        self.assertIn("build/www.bin", release_step)
        self.assertIn("fail_on_unmatched_files: true", release_step)
        self.assertNotIn("env.files", release_step)

    def test_web_build_is_reproducible_and_not_stale(self):
        cmake = (ROOT / "main/CMakeLists.txt").read_text()

        self.assertIn("include(ExternalProject)", cmake)
        self.assertIn("ci --no-audit --no-fund", cmake)
        self.assertIn("BUILD_ALWAYS TRUE", cmake)
        self.assertEqual(cmake.count('"./displays/images/ui_font_DigitalNumbers16.c"'), 1)


if __name__ == "__main__":
    unittest.main()
