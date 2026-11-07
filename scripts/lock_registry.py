# =============================================================================
# scripts/lock_registry.py: rewrites the registry's version lock
# =============================================================================
"""
Rewrites registry/registry.lock.json from registry/metrics.toml: the registry
version and a SHA-256 of each metric's canonical definition (src/registry.py).

    python scripts/lock_registry.py
    python scripts/lock_registry.py --registry <metrics.toml> --lock <registry.lock.json>

- Nothing changed: it says so and writes nothing.
- A definition changed, or a metric was added or removed, while the version
  stayed the lock's: refused, and nothing is written. Bump the version and add
  a changelog entry first.
- A new version: refused unless it comes after the locked one and the
  changelog has an entry for it naming a person (approved_by) and a date;
  otherwise the lock is rewritten.

It reads and writes only the two files; it makes no network call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import registry  # noqa: E402


def _key(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Rewrite the registry's version lock.")
    parser.add_argument("--registry", default=str(registry.REGISTRY))
    parser.add_argument("--lock", default=str(registry.LOCK))
    args = parser.parse_args(argv)
    data = Path(args.registry).read_bytes()
    try:
        current = registry.parse(data)
    except registry.RegistryError as error:
        print(f"refused: {error}")
        return 1
    new = registry.lock_for(data)
    path = Path(args.lock)
    old = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    if old == new:
        print(f"The lock is up to date for {current.version}: nothing changed, nothing written.")
        return 0
    if old is not None:
        if old.get("registry_version") == current.version:
            problems = registry.lock_problems(data, old)
            print("refused: " + "; ".join(problems) + ". Bump the version and add a changelog "
                  "entry first.")
            return 1
        if _key(current.version) <= _key(old["registry_version"]):
            print(f"refused: the version {current.version} does not come after the locked "
                  f"{old['registry_version']}.")
            return 1
    if not any(c.version == current.version for c in current.changelog):
        print(f"refused: the changelog has no entry for {current.version} naming a person and "
              "a date.")
        return 1
    changed = sorted(i for i in new["metrics"]
                     if old is None or old["metrics"].get(i) != new["metrics"][i])
    path.write_text(registry.render_lock(new), encoding="utf-8", newline="\n")
    print(f"Locked {current.version}: {len(new['metrics'])} definitions; changed since the last "
          f"lock: {', '.join(changed) or 'none'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
