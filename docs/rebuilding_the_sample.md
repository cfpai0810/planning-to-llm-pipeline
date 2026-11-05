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
3. It makes `shared_formula` by editing the XML of the recalculated small base
   (the one hand-edited fixture), and writes `no_cache`, which is never
   recalculated.
4. Only then it copies the files into `data/` and `tests/fixtures/`, writes
   `data/erp_trial_balance.csv` from the model, and writes
   `data/sample_manifest.json`: the planted truth, the checks, the hash of
   every file and the LibreOffice version.

The build is not byte for byte reproducible: LibreOffice writes the time of
the conversion into each file, so every rebuild changes the hashes in the
manifest. Commit the files and the manifest together.

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

`data/sample_manifest.json` records the same string. On the maintainer's
machine the whole build takes about 40 seconds, about 30 of them in
LibreOffice.

## Not built: external_link

The fixture with a link to another workbook is not built. Written with
openpyxl's external-link objects, LibreOffice 26.8.2.2 kept the linked formula
and its cached value but rewrote the link's target against its output folder:
`calder_plan_fy2025.xlsx` became `../written_2/calder_plan_fy2025.xlsx`. The
only hand-edited fixture allowed is `shared_formula`, so this one is left out
until the reviewer decides.
