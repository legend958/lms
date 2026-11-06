"""Verify the local textbook corpus before running ingestion or the demo seed.

The 13 source PDFs are intentionally not distributed with this repository
(copyright). They live only on the operator's machine at the repository root.
This check confirms every expected file is present and unmodified by comparing
sizes and SHA-256 hashes. It is stdlib-only so it can run on the host.

Usage:
    python scripts/verify_corpus.py
    python scripts/verify_corpus.py --dir /path/to/pdfs
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# name -> (sha256, size in bytes)
MANIFEST: dict[str, tuple[str, int]] = {
    "hesc101.pdf": (
        "0640c479963c8fb3e60eb03132e5cbbffd7dbd80ec83bb45dfd76d24eb442e64",
        2371131,
    ),
    "hesc102.pdf": (
        "9ed99dd59080f8a94a2c092b78809537247a819c20c3051deca5dc09499850dc",
        2034357,
    ),
    "hesc103.pdf": (
        "0b2ed5cca40343287a51dfc177aeb0bb0a0c5732f852970a8752ee6a8ca8f134",
        777760,
    ),
    "hesc104.pdf": (
        "d39c7be4c078f48b970f810af99d37c22e02b03fec4e6219752e38f2cea3f81f",
        1260194,
    ),
    "hesc105.pdf": (
        "19c3ef27e39a7db8f07ab7fd8f193a4d3bd3b6d45434badcc3b66eec4426f51e",
        1524696,
    ),
    "hesc106.pdf": (
        "3b72321468f309ebe8e84ed449bc9bf8833ff2693f4892fcc23190117e0182b7",
        1244889,
    ),
    "hesc108.pdf": (
        "5f90703bdf4a20e45567489035646b1883c38e6e15a8040c53832c636b0457d0",
        1868278,
    ),
    "hesc111.pdf": (
        "6565e0f88fa371b9b4ccbdaf7c99280935357f5a854f0ba73e9ddbaa3e845251",
        2147564,
    ),
    "hesc112.pdf": (
        "e505885f9d40b41c76aa41ccaee7d08524bcef2977fac5200238e02050118f4d",
        1524291,
    ),
    "hesc113.pdf": (
        "2834f664306768c3092d0e3962b00214b6c32597ec7a7a1fda0e0e9f478011f8",
        1587133,
    ),
    "hess201.pdf": (
        "9a098b768a04af04bc5508ea377d2a235f5f4768da0112cf1ce01a422ac584a7",
        6401142,
    ),
    "hess202.pdf": (
        "439adc99a27442579d4c7545c590af736688392f69eea91e4bb5172b0b397210",
        14221957,
    ),
    "hess301.pdf": (
        "075c7c0f437895a26c9b56039eee08ad4ed748908897a6db920daab0786c6216",
        4168534,
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=ROOT,
        help="directory containing the PDFs (default: repository root)",
    )
    args = parser.parse_args()

    problems = 0
    for name, (expected_hash, expected_size) in MANIFEST.items():
        path = args.dir / name
        if not path.is_file():
            print(f"MISSING   {name}")
            problems += 1
            continue
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            print(f"SIZE      {name}: expected {expected_size}, got {actual_size}")
            problems += 1
            continue
        actual_hash = sha256(path)
        if actual_hash != expected_hash:
            print(f"HASH      {name}: content does not match the manifest")
            problems += 1
            continue
        print(f"OK        {name}")

    if problems:
        print(
            f"\n{problems} of {len(MANIFEST)} corpus files are missing or different.\n"
            "The textbooks are not distributed with this repository. Obtain the\n"
            "corresponding source PDFs and place them in "
            f"{args.dir}, then re-run this check.\n"
            "If the documents were legitimately updated, refresh MANIFEST in\n"
            "scripts/verify_corpus.py and re-ingest so Postgres and Chroma match.",
            file=sys.stderr,
        )
        return 1

    print(f"\nAll {len(MANIFEST)} corpus files present and verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
