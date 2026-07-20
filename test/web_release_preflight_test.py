#!/usr/bin/env python3
"""Tests for the generated Web UI release artifact preflight."""

from __future__ import annotations

import gzip
from pathlib import Path
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_web_release as verifier  # noqa: E402


EXPECTED_VERSION = "v1.1.1-mane.6-nqa-lts7"
EXPECTED_COMMIT = "ad96b01"
OLD_VERSION = "v1.1.1-mane.6-nqa-lts5"


def _gzip(contents: str) -> bytes:
    return gzip.compress(contents.encode("utf-8"), compresslevel=9, mtime=0)


def _make_dist(root: Path, version: str, commit: str, extra_bundle_text: str = "") -> Path:
    dist = root / "dist"
    i18n = dist / "assets" / "i18n"
    i18n.mkdir(parents=True)

    bundle_name = "main.0123456789abcdef.js"
    bundle_text = (
        f'const version="{version}";version.includes("VERSION");'
        f'const commit="{commit}";commit.includes("COMMIT");'
        f"{extra_bundle_text}"
    )
    (dist / f"{bundle_name}.gz").write_bytes(_gzip(bundle_text))
    (dist / "index.html.gz").write_bytes(
        _gzip(f'<html><script src="{bundle_name}" type="module"></script></html>')
    )
    (dist / "styles.0123456789abcdef.css.gz").write_bytes(_gzip("body{color:#fff}"))

    for locale in ("en", "it"):
        payload = _gzip(f'{{"language":"{locale}"}}')
        (i18n / f"{locale}.json.gz").write_bytes(payload)
        (i18n / f"{locale}.{commit}.json.gz").write_bytes(payload)

    verifier.write_identity_manifest(dist, version, commit)

    return dist


def _write_test_spiffs(dist: Path, destination: Path) -> None:
    """Write a valid file set, including multi-block object index pages."""

    image = bytearray(b"\xff" * verifier.WWW_IMAGE_SIZE)
    page_size = verifier.SPIFFS_PAGE_SIZE
    block_size = verifier.SPIFFS_BLOCK_SIZE
    data_size = page_size - verifier.SPIFFS_DATA_HEADER_SIZE
    usable_pages = block_size // page_size - 1
    pages_per_block = block_size // page_size
    next_block = 0
    next_slot = 0

    def reserve_page(lookup_id: int) -> tuple[int, int]:
        nonlocal next_block, next_slot
        if next_slot == usable_pages:
            next_block += 1
            next_slot = 0
        if next_block * block_size >= len(image):
            raise AssertionError("test SPIFFS fixture exceeds image capacity")

        page_index = next_block * pages_per_block + 1 + next_slot
        page_offset = page_index * page_size
        lookup_offset = next_block * block_size + next_slot * verifier.SPIFFS_OBJ_ID_SIZE
        struct.pack_into("<H", image, lookup_offset, lookup_id)
        next_slot += 1
        return page_index, page_offset

    for object_id, path in enumerate(
        (path for path in sorted(dist.rglob("*")) if path.is_file()), start=1
    ):
        payload = path.read_bytes()
        chunks = [
            payload[offset : offset + data_size]
            for offset in range(0, len(payload), data_size)
        ]
        image_name = "/" + path.relative_to(dist).as_posix()
        encoded_name = image_name.encode("utf-8")
        if len(encoded_name) >= verifier.SPIFFS_OBJ_NAME_SIZE:
            raise AssertionError("test fixture filename exceeds SPIFFS name limit")

        remaining_after_header = max(
            0, len(chunks) - verifier.SPIFFS_INDEX_HEADER_POINTER_COUNT
        )
        continuation_count = (
            remaining_after_header + verifier.SPIFFS_INDEX_PAGE_POINTER_COUNT - 1
        ) // verifier.SPIFFS_INDEX_PAGE_POINTER_COUNT
        index_page_locations = [
            reserve_page(object_id | verifier.SPIFFS_INDEX_ID_MASK)
            for _ in range(continuation_count + 1)
        ]
        data_page_locations = [reserve_page(object_id) for _ in chunks]
        data_page_indexes = [page_index for page_index, _ in data_page_locations]

        _, index_page_offset = index_page_locations[0]
        index_page = bytearray(b"\xff" * page_size)
        struct.pack_into(
            "<HHB",
            index_page,
            0,
            object_id | verifier.SPIFFS_INDEX_ID_MASK,
            0,
            verifier.SPIFFS_INDEX_FLAG,
        )
        struct.pack_into("<I", index_page, 8, len(payload))
        index_page[12] = 1
        index_page[
            verifier.SPIFFS_INDEX_NAME_OFFSET : verifier.SPIFFS_INDEX_NAME_OFFSET
            + verifier.SPIFFS_OBJ_NAME_SIZE
            + 4
        ] = b"\x00" * (verifier.SPIFFS_OBJ_NAME_SIZE + 4)
        index_page[
            verifier.SPIFFS_INDEX_NAME_OFFSET : verifier.SPIFFS_INDEX_NAME_OFFSET
            + len(encoded_name)
        ] = encoded_name

        header_page_indexes = data_page_indexes[
            : verifier.SPIFFS_INDEX_HEADER_POINTER_COUNT
        ]
        for index, page_index in enumerate(header_page_indexes):
            struct.pack_into(
                "<H",
                index_page,
                verifier.SPIFFS_INDEX_HEADER_SIZE + index * verifier.SPIFFS_OBJ_ID_SIZE,
                page_index,
            )

        image[index_page_offset : index_page_offset + page_size] = index_page

        for continuation_index, (_, continuation_offset) in enumerate(
            index_page_locations[1:], start=1
        ):
            continuation_page = bytearray(b"\xff" * page_size)
            struct.pack_into(
                "<HHB",
                continuation_page,
                0,
                object_id | verifier.SPIFFS_INDEX_ID_MASK,
                continuation_index,
                verifier.SPIFFS_INDEX_FLAG,
            )
            start = verifier.SPIFFS_INDEX_HEADER_POINTER_COUNT + (
                continuation_index - 1
            ) * verifier.SPIFFS_INDEX_PAGE_POINTER_COUNT
            continuation_page_indexes = data_page_indexes[
                start : start + verifier.SPIFFS_INDEX_PAGE_POINTER_COUNT
            ]
            for index, page_index in enumerate(continuation_page_indexes):
                struct.pack_into(
                    "<H",
                    continuation_page,
                    verifier.SPIFFS_INDEX_PAGE_HEADER_SIZE
                    + index * verifier.SPIFFS_OBJ_ID_SIZE,
                    page_index,
                )
            image[
                continuation_offset : continuation_offset + page_size
            ] = continuation_page

        for span, ((_, data_page_offset), chunk) in enumerate(
            zip(data_page_locations, chunks)
        ):
            data_page = bytearray(b"\xff" * page_size)
            struct.pack_into(
                "<HHB",
                data_page,
                0,
                object_id,
                span,
                verifier.SPIFFS_DATA_FLAG,
            )
            data_page[
                verifier.SPIFFS_DATA_HEADER_SIZE : verifier.SPIFFS_DATA_HEADER_SIZE
                + len(chunk)
            ] = chunk
            image[data_page_offset : data_page_offset + page_size] = data_page

    destination.write_bytes(image)


def _corrupt_first_continuation_pointer(www: Path) -> None:
    image = bytearray(www.read_bytes())
    page_size = verifier.SPIFFS_PAGE_SIZE
    block_size = verifier.SPIFFS_BLOCK_SIZE
    usable_pages = block_size // page_size - 1

    for block_offset in range(0, len(image), block_size):
        for slot in range(usable_pages):
            lookup_id = struct.unpack_from(
                "<H", image, block_offset + slot * verifier.SPIFFS_OBJ_ID_SIZE
            )[0]
            if not lookup_id & verifier.SPIFFS_INDEX_ID_MASK:
                continue
            page_offset = block_offset + (slot + 1) * page_size
            span = struct.unpack_from("<H", image, page_offset + 2)[0]
            if span != 1:
                continue
            struct.pack_into(
                "<H",
                image,
                page_offset + verifier.SPIFFS_INDEX_PAGE_HEADER_SIZE,
                0x1234,
            )
            www.write_bytes(image)
            return

    raise AssertionError("test fixture has no continuation index page")


class WebReleasePreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_accepts_matching_dist_and_final_spiffs_image(self) -> None:
        dist = _make_dist(self.root, EXPECTED_VERSION, EXPECTED_COMMIT)
        www = self.root / "www.bin"
        _write_test_spiffs(dist, www)

        report = verifier.verify_release(EXPECTED_VERSION, EXPECTED_COMMIT, dist, www)

        self.assertTrue(report.www_verified)
        self.assertEqual(report.dist_file_count, 8)
        self.assertEqual(report.bundle, "main.0123456789abcdef.js.gz")

    def test_rejects_missing_identity_manifest(self) -> None:
        dist = _make_dist(self.root, EXPECTED_VERSION, EXPECTED_COMMIT)
        (dist / verifier.IDENTITY_FILE_NAME).unlink()

        with self.assertRaisesRegex(verifier.VerificationError, "identity manifest is missing"):
            verifier.verify_release(EXPECTED_VERSION, EXPECTED_COMMIT, dist)

    def test_rejects_identity_manifest_for_another_release(self) -> None:
        dist = _make_dist(self.root, EXPECTED_VERSION, EXPECTED_COMMIT)
        verifier.write_identity_manifest(dist, OLD_VERSION, "1a62b2e")

        with self.assertRaisesRegex(verifier.VerificationError, "identity manifest does not match"):
            verifier.verify_release(EXPECTED_VERSION, EXPECTED_COMMIT, dist)

    def test_rejects_unbounded_or_path_like_commit_identity(self) -> None:
        for commit in ("bad/commit", "x" * 65):
            with self.subTest(commit=commit):
                with self.assertRaisesRegex(verifier.VerificationError, "COMMIT_HASH must contain"):
                    verifier.build_identity_manifest(EXPECTED_VERSION, commit)

    def test_rejects_unreplaced_placeholders(self) -> None:
        dist = _make_dist(
            self.root,
            EXPECTED_VERSION,
            EXPECTED_COMMIT,
            'const oldVersion="__VERSION__";const oldCommit="__COMMIT__";',
        )

        with self.assertRaisesRegex(verifier.VerificationError, "still contains placeholders"):
            verifier.verify_release(EXPECTED_VERSION, EXPECTED_COMMIT, dist)

    def test_rejects_expected_identity_only_present_in_unrelated_constants(self) -> None:
        dist = _make_dist(
            self.root,
            "dev-wrong",
            "0000000",
            f'const expectedVersion="{EXPECTED_VERSION}";'
            f'const expectedCommit="{EXPECTED_COMMIT}";',
        )
        verifier.write_identity_manifest(dist, EXPECTED_VERSION, EXPECTED_COMMIT)

        with self.assertRaisesRegex(verifier.VerificationError, "VERSION stamp mismatch"):
            verifier.verify_release(EXPECTED_VERSION, EXPECTED_COMMIT, dist)

    def test_marker_extraction_ignores_variable_reused_in_previous_function(self) -> None:
        dist = _make_dist(self.root, EXPECTED_VERSION, EXPECTED_COMMIT)
        bundle = next(dist.glob("main.*.js.gz"))
        bundle.write_bytes(
            _gzip(
                'function unrelated(){const p="not-a-version";return p}'
                f'function version(){{const p="{EXPECTED_VERSION}";'
                'return p.includes("VERSION")?"":p}'
                f'function commit(){{const p="{EXPECTED_COMMIT}";'
                'return p.includes("COMMIT")?"":p}'
            )
        )

        report = verifier.verify_release(EXPECTED_VERSION, EXPECTED_COMMIT, dist)

        self.assertEqual(report.version, EXPECTED_VERSION)
        self.assertFalse(report.www_verified)

    def test_rejects_previous_lts_tag_even_when_expected_tag_is_present(self) -> None:
        dist = _make_dist(
            self.root,
            EXPECTED_VERSION,
            EXPECTED_COMMIT,
            f'const stale="{OLD_VERSION}";',
        )

        with self.assertRaisesRegex(verifier.VerificationError, "stale LTS tags"):
            verifier.verify_release(EXPECTED_VERSION, EXPECTED_COMMIT, dist)

    def test_rejects_missing_commit_stamped_translation(self) -> None:
        dist = _make_dist(self.root, EXPECTED_VERSION, EXPECTED_COMMIT)
        (dist / "assets" / "i18n" / f"it.{EXPECTED_COMMIT}.json.gz").unlink()

        with self.assertRaisesRegex(verifier.VerificationError, "missing commit-stamped"):
            verifier.verify_release(EXPECTED_VERSION, EXPECTED_COMMIT, dist)

    def test_rejects_www_with_different_file_contents(self) -> None:
        dist = _make_dist(self.root, EXPECTED_VERSION, EXPECTED_COMMIT)
        www = self.root / "www.bin"
        _write_test_spiffs(dist, www)
        (dist / "styles.0123456789abcdef.css.gz").write_bytes(_gzip("body{color:#000}"))

        with self.assertRaisesRegex(verifier.VerificationError, "content differs from dist"):
            verifier.verify_release(EXPECTED_VERSION, EXPECTED_COMMIT, dist, www)

    def test_rejects_www_built_from_previous_release(self) -> None:
        old_root = self.root / "old"
        old_dist = _make_dist(old_root, OLD_VERSION, "1a62b2e")
        www = self.root / "www.bin"
        _write_test_spiffs(old_dist, www)
        current_dist = _make_dist(self.root / "current", EXPECTED_VERSION, EXPECTED_COMMIT)

        with self.assertRaisesRegex(verifier.VerificationError, "file set does not match dist"):
            verifier.verify_release(EXPECTED_VERSION, EXPECTED_COMMIT, current_dist, www)

    def test_rejects_duplicate_raw_identity_outside_the_spiffs_file_set(self) -> None:
        dist = _make_dist(self.root, EXPECTED_VERSION, EXPECTED_COMMIT)
        www = self.root / "www.bin"
        _write_test_spiffs(dist, www)

        image = bytearray(www.read_bytes())
        duplicate = verifier.IDENTITY_MAGIC.encode("ascii")
        duplicate_offset = len(image) - verifier.SPIFFS_PAGE_SIZE + 32
        image[duplicate_offset : duplicate_offset + len(duplicate)] = duplicate
        www.write_bytes(image)

        with self.assertRaisesRegex(verifier.VerificationError, "exactly one raw Web identity"):
            verifier.verify_release(EXPECTED_VERSION, EXPECTED_COMMIT, dist, www)

    def test_rejects_corrupt_pointer_in_continuation_index_page(self) -> None:
        dist = _make_dist(self.root, EXPECTED_VERSION, EXPECTED_COMMIT)
        # 100 data pages exceed the 87 pointers in the object index header.
        (dist / "large.bin").write_bytes(bytes(range(251)) * 100)
        www = self.root / "www.bin"
        _write_test_spiffs(dist, www)

        valid_report = verifier.verify_release(
            EXPECTED_VERSION, EXPECTED_COMMIT, dist, www
        )
        self.assertTrue(valid_report.www_verified)
        _corrupt_first_continuation_pointer(www)

        with self.assertRaisesRegex(verifier.VerificationError, "points to missing page"):
            verifier.verify_release(EXPECTED_VERSION, EXPECTED_COMMIT, dist, www)


class WebReleasePreflightIntegrationTest(unittest.TestCase):
    def test_web_build_and_release_workflow_run_the_preflight(self) -> None:
        wrapper = (ROOT / "main/http_server/axe-os/scripts/build-versioned.sh").read_text()
        workflow = (ROOT / ".github/workflows/build.yml").read_text()

        self.assertIn("scripts/verify_web_release.py", wrapper)
        self.assertIn("scripts/verify_web_release.py", workflow)
        self.assertIn('--version "$VERSION_TAG"', wrapper)
        self.assertIn('--commit "$COMMIT_HASH"', wrapper)
        self.assertIn("--write-identity", wrapper)
        self.assertIn("--www build/www.bin", workflow)

        gzip_position = wrapper.index("node only-gzip.js")
        dist_preflight_position = wrapper.index("scripts/verify_web_release.py")
        compile_position = workflow.index("- name: Compile Binaries")
        preflight_position = workflow.index("- name: Verify Web release artifacts")
        merge_position = workflow.index("- name: Merge Binaries")
        upload_position = workflow.index("- name: Upload factory binary")
        self.assertLess(gzip_position, dist_preflight_position)
        self.assertLess(compile_position, preflight_position)
        self.assertLess(preflight_position, merge_position)
        self.assertLess(preflight_position, upload_position)


if __name__ == "__main__":
    unittest.main()
