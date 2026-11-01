# =============================================================================
# src/hashing.py: the one file hash, over raw bytes
# =============================================================================
"""
file_sha256 is the only hash of an input file. It reads raw bytes, never text,
so a hash does not depend on line endings, encoding or the machine; and
.gitattributes keeps input files byte for byte in every clone.
"""

import hashlib
from pathlib import Path

_BLOCK = 1 << 20


def file_sha256(path: Path | str) -> str:
    """SHA-256 of a file's raw bytes, as lowercase hex."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()
