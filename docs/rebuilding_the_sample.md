# Rebuilding the sample

The sample workbooks, the ledger extract and the manifest are built by one
script that only the maintainer runs. The tests and the app never need
LibreOffice: they read the committed files.

    python scripts/build_sample.py

## What the build does

1. It writes the clean workbook with openpyxl (formulas, no cached values),
   has LibreOffice recalculate it, and checks it before anything else: every
   cached value of every formula cell against `src/sample_model.py`, and every
   cell, formula, defined name, sheet and hidden state against what openpyxl
   wrote. Any difference stops the build, and nothing is written.
2. It writes the sample, the ten mutation variants and the fixtures,
   recalculates them in one LibreOffice run, and checks each the same way.
   `external_link` is hand-edited after the recalculation and checked after
   the edit (see below).
3. It makes `shared_formula` by editing the XML of the recalculated small base
   (hand-edited too), and writes `no_cache`, which is never recalculated.
4. Only then it copies the files into `data/` and `tests/fixtures/`, writes
   `data/erp_trial_balance.csv` from the model, and writes
   `data/sample_manifest.json`: the planted truth, the checks, the hash of
   every file, the hand edits and the LibreOffice version.

The build is not byte for byte reproducible: LibreOffice writes the time of
the conversion into each file, so every rebuild changes the hashes in the
manifest. Commit the files and the manifest together.

## Building only named fixtures

    python scripts/build_sample.py --only external_link extra_tab typed_subtotal

A full build changes the hash of every committed workbook. To add a fixture
without touching the files already recorded, name it with `--only` (the file
name without `.xlsx`). The build still recalculates and checks the clean
workbook first, but does not install it; it converts, checks and installs only
the named books; it does not write the ledger; and the manifest gains entries
for the named books (their checks, fixture notes and file hashes) and their
hand edits, while every other entry and every top-level field stays exactly as
it was. It refuses a book the manifest already records, a LibreOffice other
than the one the manifest records, and a manifest whose recorded files no
longer match their hashes. `external_link`, `extra_tab` and `typed_subtotal`
were added this way in part 1c, and in part 1e the nine fixtures of the
anchors, the currency and the known blind spot, in one run:

    python scripts/build_sample.py --only block_short name_shifted name_wrong_version currency_changed wrong_driver_row entity_changed name_other_tab version_label_changed name_shifted_up

Each of the nine records in the manifest the base it is made from (`base`).
`block_short`, `name_shifted`, `name_wrong_version` and `name_shifted_up` are
the small base with one defined name moved, so every cell is the small
base's. `currency_changed`, `wrong_driver_row`, `entity_changed` and
`name_other_tab` are made from the full clean workbook, because the small base
has no Plan_AT. `version_label_changed` is the small base with the label above
the ES Budget header changed. Three of them change figures, so the build
checks their cached values against the model's with the changed cells given
as specials: `wrong_driver_row` (the changed line, the EBIT it feeds, their
FY2026 totals and the Control tab's Budget EBIT), `entity_changed` (Plan_ES's
Budget and Forecast blocks compute AT's figures, because their formulas fetch
drivers by the Entity cell) and `version_label_changed` (every Budget cell
computes 0, because the formulas fetch drivers by the version label).
LibreOffice kept every defined name as it was written.

## Finding LibreOffice

The build looks in this order: the `SOFFICE` environment variable, then
`PATH`, then `C:\Program Files\LibreOffice\program\soffice.com`. The Windows
installer does not add LibreOffice to `PATH`.

## The launcher: soffice.com, never soffice.exe

On Windows, `soffice.exe --version` hangs with no output when it is run from a
shell or from a script, plain or headless with a temporary profile; it was
stopped by 30 second timeouts. `soffice.com --version` prints the version in
under a second. When the path found is `soffice.exe`, the build uses the
`soffice.com` beside it, and it stops if there is none.

## The tree kill

Each run starts in its own process group. On a timeout (600 seconds by
default; `--timeout` changes it) the build kills the whole process tree: on
Windows with `taskkill /T /F /PID <pid>`, elsewhere by killing the process
group. Killing only the launcher leaves its child `soffice.bin` running. At
the end the build lists the running `soffice.bin` processes again and reports,
and ends, any that it started.

## The profile and the output folder

Every run gets a fresh profile folder, passed as
`-env:UserInstallation=<file URI>` (a URI made by `Path.as_uri()`), so a
LibreOffice window that is open on the machine cannot block the build. The
folder is deleted after the run, also after a timeout.

Conversions write into a fresh, empty output folder, never the folder of the
input, so an input is never overwritten. The build checks that each output
exists and is not empty before reading it; a missing output is a build failure
with one plain line. A conversion runs as:

    "C:\Program Files\LibreOffice\program\soffice.com" -env:UserInstallation=file:///C:/Users/<user>/AppData/Local/Temp/lo_profile_<random> --headless --norestore --nolockcheck --convert-to xlsx --outdir <fresh folder> <workbooks>

## The version it was built with

    LibreOffice 26.8.2.2 9dea406ab7695df6325b11b0e19fdca8db4b7276

`data/sample_manifest.json` records the same string. Measured on the
maintainer's machine in part 1c: the full build of 27 workbooks took 73.8
seconds, and the partial build of three fixtures (`--only external_link
extra_tab typed_subtotal`) took 33.7 seconds, 30.8 seconds of it in
LibreOffice. In part 1e the partial build of nine fixtures took 44.0 seconds,
37.0 seconds of it in LibreOffice.

## The hand-edited fixtures

Two fixtures are edited after LibreOffice has recalculated them, each in one
part of the file, and each is checked after the edit like every other
workbook. `data/sample_manifest.json` records the edit of `external_link`
under `hand_edited`.

- `shared_formula`: the Plan_ES sheet's XML stores two groups of formulas
  (the ES Budget Product sales months, and the ES Budget FY2026 column from
  Product sales to Volume) as shared formulas, as spreadsheet applications
  write them. LibreOffice never writes shared formulas, so the fixture that
  tests their expansion has to be edited.
- `external_link`: one Control formula, `=[1]Plan_ES!$O$33`, reads a cell of
  another workbook, `calder_plan_fy2025.xlsx`. The workbook is written with
  openpyxl's external-link objects, and LibreOffice keeps the formula and its
  cached value but rewrites the link's target against its output folder, to
  `../written_2/calder_plan_fy2025.xlsx`. The build sets the `Target` of the
  one relationship in `xl/externalLinks/_rels/externalLink1.xml.rels` back to
  `calder_plan_fy2025.xlsx` and copies every other part byte for byte, so no
  committed file names a folder of the build machine. A target that names a
  drive, a user or a temporary folder is recorded with that part as
  `<work folder>`; a different file name stops the build.
