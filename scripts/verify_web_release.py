#!/usr/bin/env python3
"""Validate versioned Web UI release assets before they are published.

The ESP-IDF build stores already-gzipped Angular assets in a SPIFFS image.  A
firmware version can therefore be correct while ``www.bin`` still contains an
older UI.  This tool validates the generated ``dist`` directory and, when
provided, decodes the release SPIFFS image and compares every embedded file.

Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import math
from pathlib import Path
import re
import struct
import sys
from dataclasses import dataclass


WWW_IMAGE_SIZE = 3 * 1024 * 1024
IDENTITY_FILE_NAME = "nerdqaxe-web-identity.txt"
IDENTITY_MAGIC = "NERDQAXEPLUS_WEB_IDENTITY_V1"
IDENTITY_COMMIT_PATTERN = re.compile(r"[0-9A-Za-z._-]{1,64}\Z")
SPIFFS_BLOCK_SIZE = 4096
SPIFFS_PAGE_SIZE = 256
SPIFFS_OBJ_ID_SIZE = 2
SPIFFS_OBJ_NAME_SIZE = 64
SPIFFS_DATA_HEADER_SIZE = 5
SPIFFS_INDEX_NAME_OFFSET = 13
SPIFFS_INDEX_HEADER_SIZE = SPIFFS_INDEX_NAME_OFFSET + SPIFFS_OBJ_NAME_SIZE + 4
SPIFFS_INDEX_PAGE_HEADER_SIZE = 8
SPIFFS_INDEX_FLAG = 0xF8
SPIFFS_DATA_FLAG = 0xFC
SPIFFS_INDEX_ID_MASK = 0x8000
SPIFFS_FREE_OBJ_ID = 0xFFFF
SPIFFS_INDEX_HEADER_POINTER_COUNT = (
    SPIFFS_PAGE_SIZE - SPIFFS_INDEX_HEADER_SIZE
) // SPIFFS_OBJ_ID_SIZE
SPIFFS_INDEX_PAGE_POINTER_COUNT = (
    SPIFFS_PAGE_SIZE - SPIFFS_INDEX_PAGE_HEADER_SIZE
) // SPIFFS_OBJ_ID_SIZE

PLACEHOLDERS = ("__VERSION__", "__COMMIT__")
LTS_TAG_PATTERN = re.compile(
    r"v[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+_-]*?-nqa-lts[0-9]+"
)
JS_ASSIGNMENT_PATTERN = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r'''("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')'''
)
JS_SCOPE_BOUNDARY_PATTERN = re.compile(r"[{}]|=>|\bfunction\b")


class VerificationError(RuntimeError):
    """Raised when a release artifact does not match its expected identity."""


@dataclass(frozen=True)
class VerificationReport:
    version: str
    commit: str
    bundle: str
    dist_file_count: int
    www_verified: bool


@dataclass(frozen=True)
class _SpiffsPage:
    page_index: int
    object_id: int
    is_index: bool
    span: int
    contents: bytes


def _fail(message: str) -> None:
    raise VerificationError(message)


def _read_maybe_gzip(path: Path) -> bytes:
    contents = path.read_bytes()
    if path.suffix != ".gz":
        return contents
    try:
        return gzip.decompress(contents)
    except (EOFError, OSError) as exc:
        _fail(f"invalid gzip asset {path}: {exc}")


def _validate_identity_value(label: str, value: str) -> str:
    value = value.strip()
    if not value:
        _fail(f"{label} must not be empty")
    if not value.isascii() or any(character.isspace() for character in value):
        _fail(f"{label} must be non-empty ASCII without whitespace")
    if len(value) > 128:
        _fail(f"{label} exceeds 128 ASCII characters")
    return value


def build_identity_manifest(version: str, commit: str) -> bytes:
    version = _validate_identity_value("VERSION_TAG", version)
    commit = _validate_identity_value("COMMIT_HASH", commit)
    if IDENTITY_COMMIT_PATTERN.fullmatch(commit) is None:
        _fail(
            "COMMIT_HASH must contain 1..64 ASCII letters, digits, dots, "
            "underscores, or hyphens"
        )
    return (
        f"{IDENTITY_MAGIC}\n"
        f"VERSION_TAG={version}\n"
        f"COMMIT_HASH={commit}\n"
    ).encode("ascii")


def write_identity_manifest(dist_dir: Path, version: str, commit: str) -> Path:
    if not dist_dir.is_dir():
        _fail(f"Web UI dist directory does not exist: {dist_dir}")
    destination = dist_dir / IDENTITY_FILE_NAME
    destination.write_bytes(build_identity_manifest(version, commit))
    return destination


def _validate_identity_manifest(dist_dir: Path, version: str, commit: str) -> None:
    identity_path = dist_dir / IDENTITY_FILE_NAME
    if not identity_path.is_file():
        _fail(f"Web UI identity manifest is missing: {identity_path}")
    expected = build_identity_manifest(version, commit)
    actual = identity_path.read_bytes()
    if actual != expected:
        _fail(
            f"Web UI identity manifest does not match VERSION_TAG={version} "
            f"COMMIT_HASH={commit}: {identity_path}"
        )


def _find_unique_asset(dist_dir: Path, plain_name: str, hashed_pattern: str) -> Path:
    compressed = sorted(dist_dir.glob(f"{hashed_pattern}.gz"))
    plain = sorted(dist_dir.glob(hashed_pattern))
    candidates = compressed + plain

    # Angular normally hashes the main bundle but not index.html.  Accept the
    # unhashed fallback so the preflight also works for local/test builds.
    if not candidates:
        for suffix in (".gz", ""):
            fallback = dist_dir / f"{plain_name}{suffix}"
            if fallback.is_file():
                candidates.append(fallback)

    if len(candidates) != 1:
        rendered = ", ".join(path.name for path in candidates) or "none"
        _fail(f"expected exactly one {plain_name} asset in {dist_dir}, found: {rendered}")
    return candidates[0]


def _load_dist_files(dist_dir: Path) -> dict[str, bytes]:
    if not dist_dir.is_dir():
        _fail(f"Web UI dist directory does not exist: {dist_dir}")

    files: dict[str, bytes] = {}
    for path in sorted(dist_dir.rglob("*")):
        if path.is_symlink():
            _fail(f"Web UI dist must not contain symlinks: {path}")
        if not path.is_file():
            continue
        image_name = "/" + path.relative_to(dist_dir).as_posix()
        files[image_name] = path.read_bytes()

    if not files:
        _fail(f"Web UI dist directory is empty: {dist_dir}")
    return files


def _validate_translations(dist_dir: Path, commit: str) -> None:
    i18n_dir = dist_dir / "assets" / "i18n"
    if not i18n_dir.is_dir():
        _fail(f"translation directory is missing: {i18n_dir}")

    base_assets = sorted(i18n_dir.glob("*.json.gz")) or sorted(i18n_dir.glob("*.json"))
    base_assets = [
        path
        for path in base_assets
        if path.name.count(".") == (2 if path.suffix == ".gz" else 1)
    ]
    if not base_assets:
        _fail(f"no base translation assets found in {i18n_dir}")

    for base_asset in base_assets:
        gzip_suffix = ".gz" if base_asset.suffix == ".gz" else ""
        locale = base_asset.name.removesuffix(gzip_suffix).removesuffix(".json")
        expected_name = f"{locale}.{commit}.json{gzip_suffix}"
        expected_path = i18n_dir / expected_name
        if not expected_path.is_file():
            _fail(f"missing commit-stamped translation asset: {expected_path}")

        versioned_pattern = f"{locale}.*.json{gzip_suffix}"
        stale = sorted(
            path.name
            for path in i18n_dir.glob(versioned_pattern)
            if path.name != expected_name
        )
        if stale:
            _fail(
                f"stale commit-stamped translation assets for {locale}: "
                + ", ".join(stale)
            )


def _extract_js_marker(bundle_text: str, marker: str) -> str:
    """Return the string literal guarded by ``.includes(marker)``.

    The Angular production build renames local variables, so the variable name
    itself is not stable.  The marker check survives minification and is the
    same expression used by the Web UI to detect an unstamped local build.
    """

    values: set[str] = set()
    for assignment in JS_ASSIGNMENT_PATTERN.finditer(bundle_text):
        variable, literal = assignment.groups()
        marker_reference = re.compile(
            rf"(?<![\w$]){re.escape(variable)}\.includes\(\s*"
            rf"(['\"]){re.escape(marker)}\1\s*\)"
        )
        following_text = bundle_text[assignment.end() : assignment.end() + 512]
        marker_match = marker_reference.search(following_text)
        if marker_match is None:
            continue
        if JS_SCOPE_BOUNDARY_PATTERN.search(following_text[: marker_match.start()]):
            continue

        try:
            value = ast.literal_eval(literal)
        except (SyntaxError, ValueError):
            _fail(f"main bundle contains an invalid {marker} marker literal")
        if not isinstance(value, str):
            _fail(f"main bundle contains a non-string {marker} marker")
        values.add(value)

    if not values:
        _fail(f"main bundle does not contain the {marker} stamp marker")
    if len(values) != 1:
        rendered = ", ".join(repr(value) for value in sorted(values))
        _fail(f"main bundle contains ambiguous {marker} stamp markers: {rendered}")
    return values.pop()


def _validate_bundle(dist_dir: Path, version: str, commit: str) -> Path:
    bundle = _find_unique_asset(dist_dir, "main.js", "main.*.js")
    index = _find_unique_asset(dist_dir, "index.html", "index.html")

    try:
        bundle_text = _read_maybe_gzip(bundle).decode("utf-8")
        index_text = _read_maybe_gzip(index).decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(f"Web UI asset is not valid UTF-8: {exc}")

    served_bundle_name = bundle.name.removesuffix(".gz")
    if served_bundle_name not in index_text:
        _fail(
            f"{index.name} does not reference the validated bundle "
            f"{served_bundle_name}"
        )

    embedded_version = _extract_js_marker(bundle_text, "VERSION")
    if embedded_version != version:
        _fail(
            "main bundle VERSION stamp mismatch: "
            f"expected {version!r}, found {embedded_version!r}"
        )

    embedded_commit = _extract_js_marker(bundle_text, "COMMIT")
    if embedded_commit != commit:
        _fail(
            "main bundle COMMIT stamp mismatch: "
            f"expected {commit!r}, found {embedded_commit!r}"
        )

    remaining_placeholders = [marker for marker in PLACEHOLDERS if marker in bundle_text]
    if remaining_placeholders:
        _fail("main bundle still contains placeholders: " + ", ".join(remaining_placeholders))

    embedded_lts_tags = set(LTS_TAG_PATTERN.findall(bundle_text))
    stale_lts_tags = sorted(tag for tag in embedded_lts_tags if tag != version)
    if stale_lts_tags:
        _fail("main bundle contains stale LTS tags: " + ", ".join(stale_lts_tags))

    _validate_translations(dist_dir, commit)
    return bundle


def _decode_spiffs_image(www_path: Path) -> dict[str, bytes]:
    if not www_path.is_file():
        _fail(f"www.bin does not exist: {www_path}")

    image = www_path.read_bytes()
    if len(image) != WWW_IMAGE_SIZE:
        _fail(
            f"www.bin must be exactly {WWW_IMAGE_SIZE} bytes, found {len(image)}: "
            f"{www_path}"
        )

    pages_per_block = SPIFFS_BLOCK_SIZE // SPIFFS_PAGE_SIZE
    lookup_pages_per_block = math.ceil(
        pages_per_block * SPIFFS_OBJ_ID_SIZE / SPIFFS_PAGE_SIZE
    )
    usable_pages_per_block = pages_per_block - lookup_pages_per_block

    pages: list[_SpiffsPage] = []
    pages_by_index: dict[int, _SpiffsPage] = {}
    file_headers: dict[int, tuple[str, int]] = {}

    for block_index in range(len(image) // SPIFFS_BLOCK_SIZE):
        block_offset = block_index * SPIFFS_BLOCK_SIZE
        lookup = image[
            block_offset : block_offset + lookup_pages_per_block * SPIFFS_PAGE_SIZE
        ]

        for slot in range(usable_pages_per_block):
            lookup_id = struct.unpack_from("<H", lookup, slot * SPIFFS_OBJ_ID_SIZE)[0]
            if lookup_id == SPIFFS_FREE_OBJ_ID:
                continue

            page_offset = block_offset + (lookup_pages_per_block + slot) * SPIFFS_PAGE_SIZE
            page = image[page_offset : page_offset + SPIFFS_PAGE_SIZE]
            if len(page) != SPIFFS_PAGE_SIZE:
                _fail(f"truncated SPIFFS page at offset 0x{page_offset:x}")

            object_id = lookup_id & ~SPIFFS_INDEX_ID_MASK
            is_index = bool(lookup_id & SPIFFS_INDEX_ID_MASK)
            header_id, span, flags = struct.unpack_from("<HHB", page, 0)
            expected_header_id = lookup_id if is_index else object_id
            if header_id != expected_header_id:
                _fail(f"SPIFFS object/header mismatch at offset 0x{page_offset:x}")

            expected_flags = SPIFFS_INDEX_FLAG if is_index else SPIFFS_DATA_FLAG
            if flags != expected_flags:
                _fail(
                    f"unexpected SPIFFS page flags 0x{flags:02x} "
                    f"at offset 0x{page_offset:x}"
                )

            page_index = page_offset // SPIFFS_PAGE_SIZE
            decoded_page = _SpiffsPage(page_index, object_id, is_index, span, page)
            pages.append(decoded_page)
            pages_by_index[page_index] = decoded_page

            if is_index and span == 0:
                file_size = struct.unpack_from("<I", page, 8)[0]
                if file_size > len(image):
                    _fail(
                        f"invalid SPIFFS size {file_size} for object {object_id} "
                        f"at offset 0x{page_offset:x}"
                    )
                raw_name = page[
                    SPIFFS_INDEX_NAME_OFFSET : SPIFFS_INDEX_NAME_OFFSET + SPIFFS_OBJ_NAME_SIZE
                ]
                try:
                    name = raw_name.split(bytes((0,)), 1)[0].decode("utf-8")
                except UnicodeDecodeError as exc:
                    _fail(f"invalid SPIFFS filename at offset 0x{page_offset:x}: {exc}")
                if not name.startswith("/"):
                    _fail(f"invalid SPIFFS filename for object {object_id}: {name!r}")
                if object_id in file_headers:
                    _fail(f"duplicate SPIFFS file header for object {object_id}")
                file_headers[object_id] = (name, file_size)

    if not file_headers:
        _fail(f"www.bin contains no SPIFFS files: {www_path}")

    decoded: dict[str, bytes] = {}
    for object_id, (name, file_size) in file_headers.items():
        expected_page_count = math.ceil(
            file_size / (SPIFFS_PAGE_SIZE - SPIFFS_DATA_HEADER_SIZE)
        )

        remaining_after_header = max(
            0, expected_page_count - SPIFFS_INDEX_HEADER_POINTER_COUNT
        )
        expected_continuation_count = math.ceil(
            remaining_after_header / SPIFFS_INDEX_PAGE_POINTER_COUNT
        )
        expected_index_spans = list(range(expected_continuation_count + 1))
        index_pages = sorted(
            (page for page in pages if page.object_id == object_id and page.is_index),
            key=lambda page: page.span,
        )
        index_spans = [page.span for page in index_pages]
        if index_spans != expected_index_spans:
            _fail(
                f"SPIFFS index pages for {name} are incomplete or out of order: "
                f"expected spans {expected_index_spans}, found {index_spans}"
            )

        data_pages: list[_SpiffsPage] = []
        referenced_page_indexes: set[int] = set()
        for index_page in index_pages:
            if index_page.span == 0:
                pointer_offset = SPIFFS_INDEX_HEADER_SIZE
                pointer_capacity = SPIFFS_INDEX_HEADER_POINTER_COUNT
            else:
                pointer_offset = SPIFFS_INDEX_PAGE_HEADER_SIZE
                pointer_capacity = SPIFFS_INDEX_PAGE_POINTER_COUNT

            remaining = expected_page_count - len(data_pages)
            pointers_to_read = min(pointer_capacity, remaining)
            for pointer_entry in range(pointers_to_read):
                pointer = struct.unpack_from(
                    "<H",
                    index_page.contents,
                    pointer_offset + pointer_entry * SPIFFS_OBJ_ID_SIZE,
                )[0]
                expected_span = len(data_pages)
                if pointer == SPIFFS_FREE_OBJ_ID:
                    _fail(
                        f"SPIFFS index for {name} has an empty pointer "
                        f"for data span {expected_span}"
                    )
                if pointer in referenced_page_indexes:
                    _fail(
                        f"SPIFFS index for {name} repeats page {pointer} "
                        f"at data span {expected_span}"
                    )

                data_page = pages_by_index.get(pointer)
                if data_page is None:
                    _fail(
                        f"SPIFFS index for {name} points to missing page {pointer} "
                        f"at data span {expected_span}"
                    )
                if data_page.is_index:
                    _fail(
                        f"SPIFFS index for {name} points to index page {pointer} "
                        f"at data span {expected_span}"
                    )
                if data_page.object_id != object_id:
                    _fail(
                        f"SPIFFS index for {name} points to page {pointer} "
                        f"owned by object {data_page.object_id}"
                    )
                if data_page.span != expected_span:
                    _fail(
                        f"SPIFFS index for {name} maps data span {expected_span} "
                        f"to page {pointer} with span {data_page.span}"
                    )

                referenced_page_indexes.add(pointer)
                data_pages.append(data_page)

        if len(data_pages) != expected_page_count:
            _fail(
                f"SPIFFS index for {name} is incomplete: expected "
                f"{expected_page_count} data pages, found {len(data_pages)}"
            )

        object_data_page_indexes = {
            page.page_index
            for page in pages
            if page.object_id == object_id and not page.is_index
        }
        if object_data_page_indexes != referenced_page_indexes:
            unreferenced = sorted(object_data_page_indexes - referenced_page_indexes)
            unexpected = sorted(referenced_page_indexes - object_data_page_indexes)
            details: list[str] = []
            if unreferenced:
                details.append(f"unreferenced pages {unreferenced}")
            if unexpected:
                details.append(f"unexpected references {unexpected}")
            _fail(f"SPIFFS index/data mismatch for {name}: " + "; ".join(details))

        payload = b"".join(page.contents[SPIFFS_DATA_HEADER_SIZE:] for page in data_pages)
        if len(payload) < file_size:
            _fail(f"SPIFFS file is truncated: {name}")
        if name in decoded:
            _fail(f"duplicate SPIFFS filename: {name}")
        decoded[name] = payload[:file_size]

    return decoded


def _format_names(names: set[str]) -> str:
    ordered = sorted(names)
    visible = ordered[:8]
    rendered = ", ".join(visible)
    if len(ordered) > len(visible):
        rendered += f", ... (+{len(ordered) - len(visible)} more)"
    return rendered


def _validate_www_matches_dist(www_path: Path, dist_files: dict[str, bytes]) -> None:
    image_files = _decode_spiffs_image(www_path)
    identity_occurrences = www_path.read_bytes().count(IDENTITY_MAGIC.encode("ascii"))
    if identity_occurrences != 1:
        _fail(
            "www.bin must contain exactly one raw Web identity marker, found "
            f"{identity_occurrences}"
        )
    dist_names = set(dist_files)
    image_names = set(image_files)

    missing = dist_names - image_names
    unexpected = image_names - dist_names
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append("missing from www.bin: " + _format_names(missing))
        if unexpected:
            details.append("unexpected in www.bin: " + _format_names(unexpected))
        _fail("SPIFFS file set does not match dist (" + "; ".join(details) + ")")

    mismatched = {
        name
        for name in dist_names
        if dist_files[name] != image_files[name]
    }
    if mismatched:
        _fail("SPIFFS file content differs from dist: " + _format_names(mismatched))


def verify_release(
    version: str,
    commit: str,
    dist_dir: Path,
    www_path: Path | None = None,
) -> VerificationReport:
    version = version.strip()
    commit = commit.strip()
    if not version:
        _fail("VERSION_TAG must not be empty")
    if not commit:
        _fail("COMMIT_HASH must not be empty")

    dist_dir = dist_dir.resolve()
    _validate_identity_manifest(dist_dir, version, commit)
    bundle = _validate_bundle(dist_dir, version, commit)
    dist_files = _load_dist_files(dist_dir)

    if www_path is not None:
        _validate_www_matches_dist(www_path.resolve(), dist_files)

    return VerificationReport(
        version=version,
        commit=commit,
        bundle=bundle.name,
        dist_file_count=len(dist_files),
        www_verified=www_path is not None,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Expected VERSION_TAG")
    parser.add_argument("--commit", required=True, help="Expected COMMIT_HASH")
    parser.add_argument(
        "--dist",
        type=Path,
        default=Path("main/http_server/axe-os/dist/axe-os"),
        help="Generated Angular dist directory",
    )
    parser.add_argument(
        "--www",
        type=Path,
        help="Optional final 3 MiB SPIFFS www.bin to compare with dist",
    )
    parser.add_argument(
        "--write-identity",
        action="store_true",
        help=f"Write {IDENTITY_FILE_NAME} into dist before validation",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.write_identity:
            write_identity_manifest(args.dist.resolve(), args.version, args.commit)
        report = verify_release(args.version, args.commit, args.dist, args.www)
    except (OSError, VerificationError) as exc:
        print(f"Web release preflight failed: {exc}", file=sys.stderr)
        return 1

    scope = "dist + www.bin" if report.www_verified else "dist"
    print(
        f"Web release preflight OK ({scope}): VERSION_TAG={report.version} "
        f"COMMIT_HASH={report.commit} bundle={report.bundle} "
        f"files={report.dist_file_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
