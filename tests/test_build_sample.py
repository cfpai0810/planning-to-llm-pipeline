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


# -- Part 1c: the new fixtures, the restored link target and the partial build ----
# None of these runs LibreOffice. The partial build exists so that adding the
# three fixtures of part 1c leaves every approved file and manifest entry as
# the reviewer approved it (stop 1 verdict; the reviewer's rule of 5 November).

NEW_FIXTURES = ("external_link", "extra_tab", "typed_subtotal")
PLAIN_TARGET = "calder_plan_fy2025.xlsx"
LINK_PATH = ("http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
             "externalLinkPath")
LINK_PART = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLink"
APPROVED_VERSION = "LibreOffice 26.8.2.2 9dea406ab7695df6325b11b0e19fdca8db4b7276"
ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "sample_manifest.json"


def _linked_archive(path: Path, target: str) -> dict:
    """A workbook-shaped archive with one external link, as LibreOffice writes one."""
    parts = {
        "[Content_Types].xml": b"<Types/>",
        "xl/workbook.xml": b"<workbook><externalReferences><externalReference r:id=\"rId9\"/>"
                           b"</externalReferences></workbook>",
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="x">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId9" Type="{LINK_PART}" '
            'Target="externalLinks/externalLink1.xml"/></Relationships>').encode(),
        "xl/externalLinks/externalLink1.xml": b"<externalLink><v>3104250</v></externalLink>",
        "xl/externalLinks/_rels/externalLink1.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="x">'
            f'<Relationship Id="rId1" Type="{LINK_PATH}" Target="{target}" '
            'TargetMode="External"/>\n</Relationships>').encode(),
        "xl/worksheets/sheet1.xml": b"<c r=\"C21\"><f aca=\"false\">[1]Plan_ES!$O$33</f>"
                                    b"<v>3104250</v></c>",
    }
    import zipfile
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return parts


def _parts(path: Path) -> dict:
    import zipfile
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


class TestTheNewFixtures:
    def test_the_full_build_makes_the_three_new_fixtures(self, build):
        names = [book.dest.stem for book in build.FIXTURE_BOOKS]
        assert set(NEW_FIXTURES) <= set(names)
        for name in NEW_FIXTURES:
            book = build.BOOKS[name]
            assert book.shape == build.SMALL and book.purpose and book.expected

    def test_extra_tab_is_the_small_base_plus_one_visible_tab_of_text(self, build):
        wb, specials = build.fixture_extra_tab()
        assert specials == {}
        assert wb.sheetnames == ["Control", "Assumptions", "Plan_ES", "Lists", "Notes"]
        notes = wb["Notes"]
        assert notes.sheet_state == "visible"
        values = [c.value for row in notes.iter_rows() for c in row if c.value is not None]
        assert values and all(isinstance(v, str) and not v.startswith("=") for v in values)

    def test_typed_subtotal_types_the_june_actual_ebit_as_its_formula_gave(self, build):
        from src import sample_model as model
        wb, specials = build.fixture_typed_subtotal()
        ws = wb["Plan_ES"]
        june = ws[build.coordinate(build.SMALL, "Actual", "EBIT", "2026-06")]
        assert june.value == int(model.block("ES", "Actual")["EBIT"]["2026-06"])
        for period in model.CLOSED_PERIODS[:-1]:
            assert ws[build.coordinate(build.SMALL, "Actual", "EBIT", period)].value.startswith("=")
        assert ws[build.coordinate(build.SMALL, "Actual", "EBIT", model.FY)].value.startswith("=")

    def test_external_link_links_one_control_formula_to_the_plain_target(self, build):
        wb, specials = build.fixture_external_link()
        links = wb._external_links
        assert len(links) == 1 and links[0].file_link.Target == PLAIN_TARGET
        assert links[0].file_link.TargetMode == "External"
        ((sheet, ref), value), = specials.items()
        assert sheet == "Control"
        assert wb[sheet][ref].value == "=[1]Plan_ES!$O$33"
        assert value == 3104250


class TestTheRestoredTarget:
    def test_only_the_target_attribute_changes(self, build, tmp_path):
        source, target = tmp_path / "in.xlsx", tmp_path / "out.xlsx"
        before = _linked_archive(source, f"../written_2/{PLAIN_TARGET}")
        record = build.restore_link_target(source, target, work=tmp_path / "calder_build_x")
        after = _parts(target)
        rels = "xl/externalLinks/_rels/externalLink1.xml.rels"
        assert set(after) == set(before)
        assert {n: d for n, d in after.items() if n != rels} == \
            {n: d for n, d in before.items() if n != rels}
        assert after[rels] == before[rels].replace(f'Target="../written_2/{PLAIN_TARGET}"'.encode(),
                                                   f'Target="{PLAIN_TARGET}"'.encode())
        assert record == {"part": rels, "attribute": "Target",
                          "before": f"../written_2/{PLAIN_TARGET}", "after": PLAIN_TARGET}

    def test_a_target_already_plain_is_left_alone(self, build, tmp_path):
        source, target = tmp_path / "in.xlsx", tmp_path / "out.xlsx"
        before = _linked_archive(source, PLAIN_TARGET)
        record = build.restore_link_target(source, target, work=tmp_path)
        assert _parts(target) == before and record["before"] == record["after"] == PLAIN_TARGET

    def test_a_different_file_name_stops_the_build(self, build, tmp_path):
        source = tmp_path / "in.xlsx"
        _linked_archive(source, "../written_2/another_book.xlsx")
        with pytest.raises(build.BuildError, match="another_book.xlsx"):
            build.restore_link_target(source, tmp_path / "out.xlsx", work=tmp_path)

    @pytest.mark.parametrize("written", [
        r"C:\Users\someone\AppData\Local\Temp\calder_build_ab12\written_2\calder_plan_fy2025.xlsx",
        "file:///C:/Users/someone/AppData/Local/Temp/calder_build_ab12/written_2/"
        "calder_plan_fy2025.xlsx",
        "/tmp/calder_build_ab12/written_2/calder_plan_fy2025.xlsx",
        "../calder_build_ab12/written_2/calder_plan_fy2025.xlsx",
        "../../someone/written_2/calder_plan_fy2025.xlsx",
    ], ids=["windows path", "file uri", "posix path", "temp folder name", "user name"])
    def test_a_machine_folder_is_never_recorded(self, build, written, tmp_path):
        work = tmp_path / "calder_build_ab12"
        recorded = build.recorded_target(written, work, user="someone")
        assert recorded == "<work folder>/calder_plan_fy2025.xlsx"

    def test_a_relative_target_inside_the_build_is_recorded_as_written(self, build, tmp_path):
        recorded = build.recorded_target(f"../written_2/{PLAIN_TARGET}",
                                         tmp_path / "calder_build_ab12", user="someone")
        assert recorded == f"../written_2/{PLAIN_TARGET}"


class TestThePartialBuild:
    def test_the_only_option(self, build):
        args = build.parse_args(["--only", *NEW_FIXTURES])
        assert args.only == list(NEW_FIXTURES)
        assert build.parse_args([]).only is None

    def test_books_are_chosen_by_name(self, build):
        chosen = build.select_books(list(NEW_FIXTURES))
        assert [b.dest.stem for b in chosen] == list(NEW_FIXTURES)
        with pytest.raises(build.BuildError, match="no_such_fixture"):
            build.select_books(["no_such_fixture"])

    def test_a_partial_build_adds_books_and_never_replaces_one(self, build):
        import json
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        with pytest.raises(build.BuildError, match="shifted"):
            build.check_partial(manifest, build.select_books(["shifted"]), APPROVED_VERSION)
        with pytest.raises(build.BuildError, match="planning_model"):
            build.check_partial(manifest, build.select_books(["planning_model"]), APPROVED_VERSION)

    def test_a_partial_build_needs_the_approved_libreoffice(self, build):
        manifest = {"libreoffice": APPROVED_VERSION, "files": {}}
        books = build.select_books(["extra_tab"])
        build.check_partial(manifest, books, APPROVED_VERSION)
        with pytest.raises(build.BuildError, match="LibreOffice 26.8.3.1"):
            build.check_partial(manifest, books, "LibreOffice 26.8.3.1 0123")

    def test_the_manifest_round_trips_byte_for_byte(self):
        import json
        text = MANIFEST.read_text(encoding="utf-8")
        assert json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n" == text

    def test_the_partial_manifest_only_adds(self, build, tmp_path):
        # Every approved entry and every top-level field keeps its key, value
        # and place; the new books' entries and their hand edit are added.
        import difflib
        import json
        books = build.select_books(list(NEW_FIXTURES))
        new_rels = [b.rel for b in books]
        approved = json.loads(MANIFEST.read_text(encoding="utf-8"))
        # The manifest as approved at stop 1, without anything part 1c added.
        approved.pop("hand_edited", None)
        for key in ("files", "checks"):
            approved[key] = {k: v for k, v in approved[key].items() if k not in new_rels}
        approved["fixtures"] = [f for f in approved["fixtures"] if f["file"] not in new_rels]
        files = {}
        for book in books:
            files[book.rel] = tmp_path / book.name
            files[book.rel].write_bytes(book.name.encode())
        checks = {rel: build.Check(formula_cells=371) for rel in new_rels}
        edit = {"file": "tests/fixtures/external_link.xlsx",
                "part": "xl/externalLinks/_rels/externalLink1.xml.rels", "attribute": "Target",
                "before": f"../written_2/{PLAIN_TARGET}", "after": PLAIN_TARGET, "why": "x"}
        new = build.partial_manifest(approved, checks, books, files, [edit])
        assert list(new) == list(approved) + ["hand_edited"] and new["hand_edited"] == [edit]
        for key in approved:
            if key not in ("files", "checks", "fixtures"):
                assert new[key] == approved[key], key
        for key in ("files", "checks"):
            assert [k for k in new[key] if k in approved[key]] == list(approved[key])
            assert {k: new[key][k] for k in approved[key]} == approved[key]
            assert set(new[key]) - set(approved[key]) == set(new_rels)
        assert new["files"]["tests/fixtures/extra_tab.xlsx"]["bytes"] == len(b"extra_tab.xlsx")
        assert new["fixtures"][:len(approved["fixtures"])] == approved["fixtures"]
        assert [f["file"] for f in new["fixtures"][len(approved["fixtures"]):]] == new_rels
        before = build.render_manifest(approved).splitlines()
        after = build.render_manifest(new).splitlines()
        # As text, the new manifest is the approved one with lines inserted; the
        # only line of it that changes is the last of a block, which gains the
        # comma that JSON puts before an added entry.
        opcodes = difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
        assert "insert" in {tag for tag, *_ in opcodes}
        for tag, i1, i2, j1, j2 in opcodes:
            assert tag in ("equal", "insert", "replace"), tag
            if tag == "replace":
                assert i2 - i1 == 1 and after[j1] == before[i1] + "," and before[i1].strip() in (
                    "}", "]"), (before[i1:i2], after[j1:j2])

    def test_the_manifest_is_rendered_as_it_is_committed(self, build):
        import json
        text = MANIFEST.read_text(encoding="utf-8")
        assert build.render_manifest(json.loads(text)) == text

    def test_the_full_build_records_the_hand_edit(self, build):
        assert build.EXTERNAL_LINK.edit is build.restore_link_target


# -- Part 1e: five fixtures, built with --only -------------------------------------------------
# block_short, name_shifted and name_wrong_version change one defined name of
# the small base; currency_changed and wrong_driver_row are made from the clean
# workbook, because the small base has no Plan_AT and reconcile needs every
# entity of the ledger. None of these tests runs LibreOffice.

FIXTURES_1E = ("block_short", "name_shifted", "name_wrong_version", "currency_changed",
               "wrong_driver_row", "entity_changed", "name_other_tab", "version_label_changed",
               "name_shifted_up")


class TestThe1eFixtures:
    def test_the_full_build_makes_them_too(self, build):
        names = [book.dest.stem for book in build.FIXTURE_BOOKS]
        assert names[-9:] == list(FIXTURES_1E)
        shapes = [build.BOOKS[name].shape for name in FIXTURES_1E]
        assert shapes == [build.SMALL] * 3 + [build.Shape()] * 4 + [build.SMALL] * 2
        assert all(build.BOOKS[name].purpose and build.BOOKS[name].expected
                   for name in FIXTURES_1E)
        bases = [build.BOOKS[name].base for name in FIXTURES_1E]
        assert bases == [build.SMALL_BASE] * 3 + [build.CLEAN_BASE] * 4 + [build.SMALL_BASE] * 2
        entry = build.fixture_entry(build.BOOKS["currency_changed"])
        assert list(entry) == ["file", "purpose", "expected_in_1d", "base"]
        assert list(build.fixture_entry(build.BOOKS["extra_tab"])) == [
            "file", "purpose", "expected_in_1d"]

    @pytest.mark.parametrize("name, moved, refers_to", [
        ("block_short", "Budget_ES", "Plan_ES!$A$22:$O$33"),
        ("name_shifted", "Budget_ES", "Plan_ES!$A$23:$O$36"),
        ("name_wrong_version", "Forecast_ES", "Plan_ES!$A$22:$O$35"),
        ("name_shifted_up", "Budget_ES", "Plan_ES!$A$21:$O$34"),
    ])
    def test_one_defined_name_of_the_small_base_moves(self, build, name, moved, refers_to):
        wb, specials = build.BOOKS[name].make()
        base = build.write_workbook(build.SMALL)
        names = {n: d.attr_text for n, d in wb.defined_names.items()}
        small = {n: d.attr_text for n, d in base.defined_names.items()}
        assert specials == {} and set(names) == set(small)
        assert {n for n in names if names[n] != small[n]} == {moved}
        assert names[moved] == refers_to

    def test_currency_changed_sets_one_cell(self, build):
        wb, specials = build.fixture_currency_changed()
        assert specials == {} and wb.sheetnames == ["Control", "Assumptions", "Plan_ES",
                                                    "Plan_AT", "Plan_DE", "Lists"]
        assert (wb["Plan_AT"]["A3"].value, wb["Plan_AT"]["B3"].value) == ("Currency", "USD")
        assert wb["Plan_ES"]["B3"].value == wb["Plan_DE"]["B3"].value == "EUR"

    def test_wrong_driver_row_fetches_it_services_in_every_month(self, build):
        from src import sample_model as model
        wb, specials = build.fixture_wrong_driver_row()
        ws = wb["Plan_DE"]
        rent = [ws[build.coordinate(build.Shape(), "Budget", "Rent and facilities", p)].value
                for p in model.PERIODS]
        assert all('"IT services"' in f and '"Rent"' not in f for f in rent)
        # 12 months and FY2026 of the line and of EBIT, and the Control tab's Budget EBIT.
        assert len(specials) == 27
        assert specials[("Plan_DE", "O31")] == 12 * 33800
        assert specials[("Plan_DE", "O33")] == model.block("DE", "Budget")["EBIT"]["FY2026"] \
            + 12 * (86000 - 33800)

    def test_name_other_tab_points_budget_at_into_plan_es(self, build):
        wb, specials = build.fixture_name_other_tab()
        clean = build.write_workbook()
        names = {n: d.attr_text for n, d in wb.defined_names.items()}
        before = {n: d.attr_text for n, d in clean.defined_names.items()}
        assert specials == {} and {n for n in names if names[n] != before[n]} == {"Budget_AT"}
        assert names["Budget_AT"] == "Plan_ES!$A$22:$O$35"

    def test_entity_changed_and_version_label_changed_give_their_new_figures(self, build):
        from src import sample_model as model
        wb, specials = build.fixture_entity_changed()
        assert wb["Plan_ES"]["B2"].value == "AT"
        # 2 versions x 13 lines x 13 columns on Plan_ES, and 6 Control cells.
        assert len(specials) == 2 * 13 * 13 + 6
        assert specials[("Plan_ES", "O33")] == model.block("AT", "Budget")["EBIT"]["FY2026"]
        wb, specials = build.fixture_version_label_changed()
        assert wb["Plan_ES"]["A21"].value == "Plan B"
        assert len(specials) == 13 * 13 + 3 and set(specials.values()) == {0}

    def test_a_partial_build_never_rebuilds_them(self, build):
        import json
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for name in FIXTURES_1E:
            with pytest.raises(build.BuildError, match=name):
                build.check_partial(manifest, build.select_books([name]), APPROVED_VERSION)
            entry = next(f for f in manifest["fixtures"]
                         if f["file"] == f"tests/fixtures/{name}.xlsx")
            assert entry["purpose"] and entry["expected_in_1d"]


# -- Part 2a: seven fixtures, built with --only ---------------------------------------------------
# Each is made from the full clean workbook (write_workbook()) and records it as its base.
# None of these tests runs LibreOffice.

FIXTURES_2A = ("renamed_feeds_subtotal", "duplicate_account", "unit_column_empty",
               "extra_version_block", "currency_lowercase", "currency_trailing_space",
               "version_label_lowercase")


def _cells(wb) -> dict:
    return {(ws.title, c.coordinate): c.value for ws in wb.worksheets for row in ws.iter_rows()
            for c in row if c.value is not None}


class TestThe2aFixtures:
    def test_the_full_build_makes_them_after_the_1e_fixtures(self, build):
        assert [book.dest.stem for book in build.FIXTURE_BOOKS_2A] == list(FIXTURES_2A)
        assert build.STEP_2[-1] is build.BASE
        assert build.STEP_2[-1 - len(FIXTURES_2A):-1] == build.FIXTURE_BOOKS_2A
        for name in FIXTURES_2A:
            book = build.BOOKS[name]
            assert book.shape == build.Shape() and book.base == build.CLEAN_BASE, name
            assert book.purpose and book.expected, name

    @pytest.mark.parametrize("name, changed", [
        ("renamed_feeds_subtotal", {("Plan_ES", "A45"): "Staff costs"}),
        ("duplicate_account", {("Plan_ES", "A25"): "Salaries"}),
        ("unit_column_empty", {("Plan_ES", f"B{r}"): None for r in range(23, 36)}),
        ("currency_lowercase", {("Plan_AT", "B3"): "eur"}),
        ("currency_trailing_space", {("Plan_AT", "B3"): "EUR "}),
        ("version_label_lowercase", {("Plan_ES", "A21"): "budget"}),
    ])
    def test_each_changes_the_clean_workbook_in_its_own_cells(self, build, name, changed):
        wb, specials = build.BOOKS[name].make()
        mine, clean = _cells(wb), _cells(build.write_workbook())
        assert specials == {}
        assert {k for k in set(mine) | set(clean) if mine.get(k) != clean.get(k)} == set(changed)
        assert {k: mine.get(k) for k in changed} == changed

    def test_extra_version_block_adds_a_block_whose_cells_compute_zero(self, build):
        from src import sample_model as model
        wb, specials = build.fixture_extra_version_block()
        mine, clean = _cells(wb), _cells(build.write_workbook())
        added = {k for k in mine if k not in clean}
        assert {k for k in clean if mine.get(k) != clean[k]} == set()
        assert {s for s, _ in added} == {"Plan_ES"}
        rows = {int("".join(ch for ch in k if ch.isdigit())) for _, k in added}
        assert rows == set(range(53, 68))
        assert len(specials) == 13 * 13 and set(specials.values()) == {0}
        assert set(specials) == {k for k in added if str(mine[k]).startswith("=")}
        assert wb["Plan_ES"]["A53"].value == "Plan B" and model.FY == wb["Plan_ES"]["O54"].value

    def test_a_partial_build_never_rebuilds_them(self, build):
        import json
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for name in FIXTURES_2A:
            with pytest.raises(build.BuildError, match=name):
                build.check_partial(manifest, build.select_books([name]), APPROVED_VERSION)
            entry = next(f for f in manifest["fixtures"]
                         if f["file"] == f"tests/fixtures/{name}.xlsx")
            assert entry["base"] == build.CLEAN_BASE and entry["purpose"] and entry["expected_in_1d"]
