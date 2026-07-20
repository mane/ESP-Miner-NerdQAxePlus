#!/usr/bin/env python3
"""Static checks for reproducible firmware/Web UI version stamping."""

from pathlib import Path
import ast
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BuildVersionStampTests(unittest.TestCase):
    BUILDER_IMAGE = (
        "shufps/esp-idf-builder:0.0.1@sha256:"
        "08101278a9ab09568cda93cac5a4927a897cbe08b9008253f9472ca9d17d0917"
    )

    def test_explicit_version_is_set_before_esp_idf_project_setup(self):
        cmake = (ROOT / "CMakeLists.txt").read_text()
        version_pos = cmake.index('set(PROJECT_VER "$ENV{VERSION_TAG}")')
        idf_setup_pos = cmake.index("include($ENV{IDF_PATH}/tools/cmake/project.cmake)")

        self.assertLess(version_pos, idf_setup_pos)

    def test_docker_wrappers_forward_host_version_and_commit(self):
        for relative_path in ("docker/idf.sh", "docker/idf-shell.sh"):
            script = (ROOT / relative_path).read_text()
            with self.subTest(script=relative_path):
                self.assertIn('commit_hash="${COMMIT_HASH:-$(git -C "$repo_dir" rev-parse --short HEAD', script)
                self.assertIn('version_tag="${VERSION_TAG:-dev-${commit_hash}}"', script)
                self.assertIn('diff-index --quiet HEAD --', script)
                self.assertIn('version_tag="${version_tag}-dirty"', script)
                self.assertNotIn('describe --tags --abbrev=0', script)
                self.assertIn('-e VERSION_TAG="$version_tag"', script)
                self.assertIn('-e COMMIT_HASH="$commit_hash"', script)

    def test_versioned_web_build_has_safe_non_git_fallbacks(self):
        script = (ROOT / "main/http_server/axe-os/scripts/build-versioned.sh").read_text()

        self.assertIn('COMMIT_HASH="${COMMIT_HASH:-local}"', script)
        self.assertIn('if [[ -z "${VERSION_TAG:-}" ]]', script)
        self.assertIn('VERSION_TAG="dev-${COMMIT_HASH}"', script)
        self.assertIn('VERSION_TAG="${VERSION_TAG}-dirty"', script)
        self.assertIn('diff-index --quiet HEAD --', script)
        self.assertNotIn('describe --tags --abbrev=0', script)

    def test_release_workflow_forwards_version_to_the_container(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()

        self.assertIn('echo "COMMIT_HASH=${commit_hash}" >> "$GITHUB_ENV"', workflow)
        self.assertGreaterEqual(workflow.count("-e VERSION_TAG -e COMMIT_HASH"), 2)

    def test_release_inputs_are_pinned_and_locked(self):
        lock = (ROOT / "dependencies.lock").read_text()
        self.assertIn('target: esp32s3', lock)
        self.assertIn('version: 5.3.0', lock)

        for relative_path in (
            ".github/workflows/build.yml",
            ".github/workflows/validate.yml",
            "readme.md",
        ):
            source = (ROOT / relative_path).read_text()
            with self.subTest(source=relative_path):
                self.assertIn(self.BUILDER_IMAGE, source)
                self.assertNotIn("shufps/esp-idf-builder:0.0.1 ", source)

    def test_ci_runs_firmware_and_web_regressions(self):
        for relative_path in (
            ".github/workflows/build.yml",
            ".github/workflows/validate.yml",
        ):
            workflow = (ROOT / relative_path).read_text()
            with self.subTest(workflow=relative_path):
                self.assertIn("python3 -m unittest discover -s test -p '*test.py'", workflow)
                self.assertIn("npm run test:build-tools", workflow)
                self.assertIn("npm test -- --watch=false --browsers=ChromeHeadless", workflow)
                self.assertLess(
                    workflow.index("npm run build"),
                    workflow.index("idf.py build"),
                )
                self.assertIn("-e GITHUB_ACTIONS=true", workflow)
                self.assertIn("node-version: 20.16.0", workflow)

    def test_unittest_discovery_wraps_top_level_test_functions(self):
        for path in sorted((ROOT / "test").glob("*test.py")):
            tree = ast.parse(path.read_text())
            top_level_tests = [
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
            ]
            if not top_level_tests:
                continue
            load_tests = [
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "load_tests"
            ]
            self.assertTrue(
                load_tests,
                f"{path.name} has top-level tests hidden from unittest: "
                + ", ".join(top_level_tests),
            )

    def test_local_builder_matches_pinned_release_toolchain(self):
        dockerfile = (ROOT / "docker/Dockerfile").read_text()
        self.assertIn(f"FROM {self.BUILDER_IMAGE}", dockerfile)
        self.assertNotIn("espressif/idf:v5.3.3", dockerfile)

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
