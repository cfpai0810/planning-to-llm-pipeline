# Releasing

Nothing in Phase 0 is deployed. These are the rules every later release
follows.

## The reboot rule

The owner reboots the app after every push to a deployed branch. On Streamlit
Community Cloud: share.streamlit.io, the app's menu, Reboot app; then open
every page. The platform pulls a push into the running process without
restarting it and keeps old modules loaded, so a page can meet an older copy
of a module it imports. The reboot is required, not a fallback, and the app
carries no code to work around a pull without a reboot.

## The lock files

`requirements.in` holds the direct pins, with the reason for each one that is
held back. `requirements.txt` pins every package, transitive ones included,
for Python 3.12 and later on every platform:

    uv pip compile requirements.in --universal --python-version 3.12 -o requirements.txt

`requirements-dev.in` adds the test tools on top of that exact lock:

    uv pip compile requirements-dev.in --universal --python-version 3.12 -o requirements-dev.txt

After changing a pin, run both commands in that order and commit the four
files together. The lock has no hashes yet: whether Streamlit Community Cloud
accepts a lock with hashes has not been verified. CI installs
`requirements.txt` and `requirements-dev.txt` on Python 3.13 and 3.14, and a
test fails on any lock line that is not an exact pin, or on any dependency the
lock leaves out.

## The test count

A full, passing run of the whole suite (`python -m pytest`) writes
`generated/test_counts.json` and the count line in the README, both from the
same numbers, so the README never holds a typed count. A partial run (one
file, a node id, a `-k` or `-m` filter, a deselection, `--lf`, `--ignore`,
`--collect-only`) or a failing run writes nothing.

When tests are added or removed, the test that compares the README count with
`python -m pytest --collect-only` fails. Run `python -m pytest --update-counts`
once: it skips the three checks of the committed count, so the full, passing
run can write the new one. Then run `python -m pytest` again, which must pass, and commit the
count with the tests.
