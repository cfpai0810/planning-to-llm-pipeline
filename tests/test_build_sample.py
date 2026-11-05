"""How the sample build runs LibreOffice (scripts/build_sample.py), part 1b.

Only the maintainer's build runs LibreOffice; these tests never do. They check
the reviewer's decisions of 5 November: the search order (the SOFFICE variable,
then PATH, then the default install), soffice.com in place of soffice.exe
(which hangs on Windows), a fresh profile folder per run passed as a file URI
and deleted afterwards even after a timeout, a fresh output folder that is
never the input's, a missing output as one plain line, and a timeout that kills
the whole process tree, because a killed launcher leaves its child running.
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from tests.conftest import subprocess_env

WINDOWS = os.name == "nt"
LAUNCHER = "soffice.com" if WINDOWS else "soffice"


@pytest.fixture(scope="module")
def build():
    from scripts import build_sample
    return build_sample


def _launcher(folder: Path, name: str = LAUNCHER) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"")
    path.chmod(0o755)
    return path


def _same(a, b) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _folder_of(argument: str) -> Path:
    assert argument.startswith("-env:UserInstallation=file:")
    return Path(url2pathname(urlparse(argument.split("=", 1)[1]).path))


class TestFindingLibreOffice:
    def test_the_variable_comes_first(self, build, tmp_path):
        named = _launcher(tmp_path / "named")
        on_path = _launcher(tmp_path / "path")
        found = build.find_soffice({"SOFFICE": str(named), "PATH": str(on_path.parent)})
        assert _same(found, named)

    def test_then_the_path(self, build, tmp_path):
        on_path = _launcher(tmp_path / "path")
        assert _same(build.find_soffice({"PATH": str(on_path.parent)}), on_path)

    def test_then_the_default_install(self, build, tmp_path, monkeypatch):
        default = _launcher(tmp_path / "program", "soffice.com")
        monkeypatch.setattr(build, "DEFAULT_SOFFICE", default)
        assert _same(build.find_soffice({"PATH": ""}), default)

    def test_nothing_found(self, build, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "DEFAULT_SOFFICE", tmp_path / "absent" / "soffice.com")
        assert build.find_soffice({"PATH": ""}) is None

    def test_soffice_exe_is_replaced_by_the_com_beside_it(self, build, tmp_path):
        exe = _launcher(tmp_path / "program", "soffice.exe")
        com = _launcher(tmp_path / "program", "soffice.com")
        assert _same(build.find_soffice({"SOFFICE": str(exe), "PATH": ""}), com)

    def test_soffice_exe_alone_is_refused(self, build, tmp_path):
        exe = _launcher(tmp_path / "program", "soffice.exe")
        with pytest.raises(build.BuildError, match="soffice.com"):
            build.find_soffice({"SOFFICE": str(exe), "PATH": ""})

    def test_the_documented_default(self, build):
        assert str(build.DEFAULT_SOFFICE) == r"C:\Program Files\LibreOffice\program\soffice.com"


class TestTheCommand:
    def test_the_conversion_command(self, build, tmp_path):
        profile, out = tmp_path / "profile", tmp_path / "out"
        books = [tmp_path / "in" / "a.xlsx", tmp_path / "in" / "b.xlsx"]
        command = build.conversion_command(Path("soffice.com"), profile, out, books)
        assert command == ["soffice.com", f"-env:UserInstallation={profile.as_uri()}",
                           "--headless", "--norestore", "--nolockcheck",
                           "--convert-to", "xlsx", "--outdir", str(out), *map(str, books)]

    def test_the_profile_is_a_proper_file_uri(self, build, tmp_path):
        argument = build.profile_argument(tmp_path / "a folder")
        assert argument == f"-env:UserInstallation={(tmp_path / 'a folder').as_uri()}"
        assert "\\" not in argument and _same(_folder_of(argument), tmp_path / "a folder")

    @pytest.mark.skipif(not WINDOWS, reason="the Windows tree kill")
    def test_the_windows_tree_kill(self, build):
        assert build.tree_kill_command(4242) == ["taskkill", "/T", "/F", "/PID", "4242"]


class TestTheProfileAndTheOutput:
    def test_the_profile_is_fresh_and_deleted_even_after_a_timeout(self, build, monkeypatch):
        seen = []

        def timing_out(argv, timeout, env=None):
            folder = _folder_of(argv[1])
            seen.append((folder, folder.is_dir(), list(folder.iterdir())))
            raise build.BuildError("LibreOffice timed out")

        monkeypatch.setattr(build, "run_tree", timing_out)
        for _ in range(2):
            with pytest.raises(build.BuildError):
                build.run_soffice(Path("soffice.com"), ["--version"], timeout=1)
        (first, existed, contents), (second, _, _) = seen
        assert existed and contents == [] and first != second
        assert not first.exists() and not second.exists()

    def test_the_output_folder_is_never_the_input_folder(self, build, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "run_soffice", lambda *a, **k: pytest.fail("ran LibreOffice"))
        book = tmp_path / "in" / "a.xlsx"
        book.parent.mkdir()
        book.write_bytes(b"PK")
        with pytest.raises(build.BuildError, match="output folder"):
            build.convert(Path("soffice.com"), [book], book.parent, timeout=1)

    def test_the_output_folder_must_be_fresh(self, build, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "run_soffice", lambda *a, **k: pytest.fail("ran LibreOffice"))
        book = tmp_path / "in" / "a.xlsx"
        book.parent.mkdir()
        book.write_bytes(b"PK")
        out = tmp_path / "out"
        out.mkdir()
        (out / "a.xlsx").write_bytes(b"old")
        with pytest.raises(build.BuildError, match="output folder"):
            build.convert(Path("soffice.com"), [book], out, timeout=1)

    @pytest.mark.parametrize("written", [None, b""], ids=["missing", "empty"])
    def test_a_missing_or_empty_output_is_one_plain_line(self, build, tmp_path, monkeypatch,
                                                         written):
        book = tmp_path / "in" / "a.xlsx"
        book.parent.mkdir()
        book.write_bytes(b"PK")
        out = tmp_path / "out"

        def soffice(path, args, timeout):
            if written is not None:
                (out / "a.xlsx").write_bytes(written)

        monkeypatch.setattr(build, "run_soffice", soffice)
        with pytest.raises(build.BuildError) as caught:
            build.convert(Path("soffice.com"), [book], out, timeout=1)
        message = str(caught.value)
        assert "\n" not in message and "a.xlsx" in message


class TestTheTreeKill:
    def test_a_timeout_kills_the_child_and_its_child(self, build, tmp_path):
        # The child starts a grandchild, as soffice.com starts soffice.bin, and
        # both would sleep for a minute.
        marker = tmp_path / "grandchild.pid"
        code = ("import subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
                "time.sleep(60)\n")
        started = time.monotonic()
        with pytest.raises(build.BuildError, match="timed out"):
            build.run_tree([sys.executable, "-c", code], timeout=4, env=subprocess_env())
        assert time.monotonic() - started < 30
        grandchild = int(marker.read_text())
        deadline = time.monotonic() + 10
        while build.process_running(grandchild) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not build.process_running(grandchild)

    def test_a_finished_run_returns_its_output(self, build):
        result = build.run_tree([sys.executable, "-c", "print('LibreOffice 1.2.3.4 abc')"],
                                timeout=30, env=subprocess_env())
        assert result.returncode == 0 and result.stdout.strip() == "LibreOffice 1.2.3.4 abc"

    def test_the_running_check_sees_this_process(self, build):
        assert build.process_running(os.getpid())
        finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                                  capture_output=True, text=True, env=subprocess_env())
        assert not build.process_running(int(finished.stdout))
