"""file_sha256: one hash over a file's raw bytes, as P6.

The audit will record input hashes of raw bytes, so a hash must not depend on
line endings, encoding or the machine: .gitattributes keeps inputs byte for
byte, and the hash reads bytes, never text.
"""

import hashlib

from src.hashing import file_sha256

ROWS_LF = b"member,value\nRevenue,100\n"
ROWS_CRLF = b"member,value\r\nRevenue,100\r\n"


class TestFileSha256:
    def test_the_hash_of_the_raw_bytes(self, tmp_path):
        path = tmp_path / "plan.csv"
        path.write_bytes(ROWS_LF)
        assert file_sha256(path) == hashlib.sha256(ROWS_LF).hexdigest()
        assert file_sha256(str(path)) == file_sha256(path)

    def test_line_endings_are_not_normalised(self, tmp_path):
        lf, crlf = tmp_path / "lf.csv", tmp_path / "crlf.csv"
        lf.write_bytes(ROWS_LF)
        crlf.write_bytes(ROWS_CRLF)
        assert file_sha256(lf) != file_sha256(crlf)
        assert file_sha256(crlf) == hashlib.sha256(ROWS_CRLF).hexdigest()

    def test_bytes_that_are_not_text_hash_too(self, tmp_path):
        path = tmp_path / "book.xlsx"
        data = bytes(range(256)) * 3
        path.write_bytes(data)
        assert file_sha256(path) == hashlib.sha256(data).hexdigest()
        assert len(file_sha256(path)) == 64
