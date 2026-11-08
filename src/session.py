# =============================================================================
# src/session.py: one visitor's warehouse
# =============================================================================
"""
A session is one visitor's warehouse (the reviewer's Phase 2 brief, 5.3): a
copy of the template (startup.build_template) in a fresh folder of its own,
opened read-only through warehouse.connect. Its acknowledgements, its
promotion and its facts live in its own file, so one visitor's promotion
never changes another's answers.

- Reading: reading() gives one cursor per request, taken from the session's
  read-only connection, and closes it after.
- Writing: DuckDB refuses a read-write and a read-only connection to one file
  in one process, so a write runs as a cycle: writing() closes the read-only
  connection, opens a read-write one, yields it, closes it and reopens the
  read-only one. A lock that many readers or one writer may hold keeps every
  query out of a write cycle, and a write cycle waits for the queries running.
  acknowledge_many() and promote() each run in one cycle; promote wraps
  promotion.promote (a named promoter, one transaction, the facts rebuilt).
- promoted() gives the promoted pair, or the outcome nothing_promoted.
- provenance() gives what an answer card needs (5.5), reading only and
  naming no cell content.

The copy is made with shutil.copyfile, which does not carry the template's
read-only bit (shutil.copy would, and the write cycle would then fail).

Bounds (Sessions): at most config.MAX_SESSIONS sessions are open; opening one
more closes the least recently used, by its last read or write. A session's
folder is removed when it closes and, for every session still open, when the
process exits (atexit). A pool removes, when it starts, the folders a crashed
process left behind: those under its root whose name has the fixed prefix and
which are older than one day.

The session reaches the database through warehouse.connect alone and imports
neither the engine nor the query parser (tests/test_boundaries.py).
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
import threading
import time
from collections import Counter, OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Optional, Union

import config
from src import outcomes, promotion, registry, warehouse
from src.batch import Pair
from src.controls import SEVERITIES
from src.startup import Template

__all__ = ["PREFIX", "STALE_SECONDS", "Session", "SessionError", "Sessions", "remove_stale"]

PREFIX = "p7_session_"
STALE_SECONDS = 24 * 60 * 60
_LIVE: set = set()                   # every open session of the process, closed at exit


class SessionError(Exception):
    """A session that cannot do what was asked, told in one plain line."""


class _ReadersAndWriter:
    """Many readers or one writer. A writer waits for the readers running and holds new ones
    back while it waits; a thread that already reads may read again."""

    def __init__(self) -> None:
        self._state = threading.Condition()
        self._readers = 0
        self._writing = False
        self._waiting = 0
        self._mine = threading.local()

    @contextmanager
    def read(self):
        depth = getattr(self._mine, "depth", 0)
        with self._state:
            while self._writing or (self._waiting and not depth):
                self._state.wait()
            self._readers += 1
        self._mine.depth = depth + 1
        try:
            yield
        finally:
            self._mine.depth = depth
            with self._state:
                self._readers -= 1
                self._state.notify_all()

    @contextmanager
    def write(self):
        with self._state:
            self._waiting += 1
            while self._writing or self._readers:
                self._state.wait()
            self._waiting -= 1
            self._writing = True
        try:
            yield
        finally:
            with self._state:
                self._writing = False
                self._state.notify_all()


class Session:
    """One visitor's copy of the template, read-only between write cycles."""

    def __init__(self, template: Template, folder: Path | str,
                 on_use: Optional[Callable] = None, on_close: Optional[Callable] = None):
        self.template = template
        self.pairs = dict(template.pairs)
        self.folder = Path(folder)
        self.path = self.folder / warehouse.DATABASE_NAME
        self.closed = False
        self._on_use, self._on_close = on_use, on_close
        self._lock = _ReadersAndWriter()
        shutil.copyfile(template.path, self.path)
        self._con = warehouse.connect(self.path, read_only=True)
        _LIVE.add(self)

    # -- Reading and writing -------------------------------------------------------------------

    def _open(self) -> None:
        if self.closed:
            raise SessionError("This session is closed.")
        if self._on_use is not None:
            self._on_use(self)

    @contextmanager
    def reading(self):
        """One cursor of the read-only connection, for one request; closed after."""
        self._open()
        with self._lock.read():
            if self.closed:
                raise SessionError("This session is closed.")
            cursor = self._con.cursor()
            try:
                yield cursor
            finally:
                cursor.close()

    @contextmanager
    def writing(self):
        """A write cycle: the read-only connection closed, a read-write one yielded and closed,
        the read-only one reopened; no query runs meanwhile."""
        self._open()
        with self._lock.write():
            if self.closed:
                raise SessionError("This session is closed.")
            self._con.close()
            try:
                con = warehouse.connect(self.path)
                try:
                    yield con
                finally:
                    con.close()
            finally:
                self._con = warehouse.connect(self.path, read_only=True)

    def _pair(self, pair: Union[str, Pair]) -> Pair:
        if isinstance(pair, Pair):
            return pair
        if pair not in self.pairs:
            raise SessionError(f"The session has no pair called {pair!r}.")
        return self.pairs[pair]

    # -- The gate, in one cycle each ----------------------------------------------------------

    def breaks(self, pair: Union[str, Pair]) -> tuple:
        """The pair's stored breaks, in the order they were stored."""
        with self.reading() as cursor:
            return promotion.breaks_of(cursor, self._pair(pair))

    def acknowledge_many(self, pair: Union[str, Pair], items: Iterable,
                         *, clock: Callable = promotion.utc_now) -> tuple:
        """Acknowledges each (break id, person, action[, note]) of the pair in one write cycle.

        Each is promotion.acknowledge's own append, refused with its fixed sentence; the
        ones given before a refusal stay, as every acknowledgement does.
        """
        pair, items = self._pair(pair), [tuple(item) for item in items]
        with self.writing() as con:
            return tuple(promotion.acknowledge(con, pair, item[0], item[1], item[2],
                                               item[3] if len(item) > 3 else None, clock=clock)
                         for item in items)

    def promote(self, pair: Union[str, Pair], person, *,
                clock: Callable = promotion.utc_now) -> promotion.Promotion:
        """A named person promotes the pair, and the facts are rebuilt, in one write cycle."""
        pair = self._pair(pair)
        with self.writing() as con:
            return promotion.promote(con, pair, person, clock=clock)

    def promoted(self) -> Union[promotion.Promotion, outcomes.Outcome]:
        """The latest promotion of this session, or the outcome nothing_promoted."""
        with self.reading() as cursor:
            found = promotion.promoted_batch(cursor)
        return found if found is not None else outcomes.NOTHING_PROMOTED

    def members(self) -> Union[dict, outcomes.Outcome]:
        """The promoted pair's entities (code, name, region) and accounts (code, name), in
        their list orders, for src/roles.py; or the outcome nothing_promoted."""
        with self.reading() as cursor:
            if promotion.promoted_batch(cursor) is None:
                return outcomes.NOTHING_PROMOTED
            entities = cursor.execute("SELECT entity, entity_name, region FROM dim_entity "
                                      "ORDER BY list_order").fetchall()
            accounts = cursor.execute("SELECT account, account_name FROM dim_account "
                                      "ORDER BY list_order").fetchall()
        return {"entities": tuple(entities), "accounts": tuple(accounts)}

    # -- Provenance (5.5) -------------------------------------------------------------------------

    def provenance(self, role=None) -> dict:
        """What an answer card needs, read and never written: no cell content.

        The promoted pair's plan batch (id, file name and hash) and ledger batch (id, file
        name and hash), the promotion (number, person, time), the pair's breaks by severity
        and how many have an acknowledgement and, by each break's latest one, how many are
        accepted, dismissed or investigated; the registry's version and hash; the engine and
        its version; and, given a role, its view. With nothing promoted it says so.
        """
        loaded = registry.load()
        tail = {"registry_version": loaded.version, "registry_hash": loaded.sha256,
                "engine": warehouse.ENGINE, "engine_version": warehouse.ENGINE_VERSION}
        view = {} if role is None else {"view": role.view()}
        with self.reading() as cursor:
            found = promotion.promoted_batch(cursor)
            if found is None:
                nothing = outcomes.NOTHING_PROMOTED
                return {"promoted": False, "outcome": nothing.code, "sentence": nothing.sentence,
                        **view, **tail}
            pair = found.pair
            files = dict((b, (name, sha)) for b, name, sha in cursor.execute(
                "SELECT batch_id, file_name, file_sha256 FROM raw_batch WHERE batch_id IN (?, ?)",
                [pair.plan_batch_id, pair.ledger_batch_id]).fetchall())
            breaks = promotion.breaks_of(cursor, pair)
            given = promotion.acknowledgements(cursor, pair)
        latest = {a.break_id: a.action for a in given}           # in order: the last one wins
        ids = {b.break_id for b in breaks}
        actions = Counter(action for break_id, action in latest.items() if break_id in ids)
        severities = Counter(b.severity for b in breaks)
        return {"promoted": True,
                "plan_batch_id": pair.plan_batch_id,
                "plan_file_name": files[pair.plan_batch_id][0],
                "plan_file_sha256": files[pair.plan_batch_id][1],
                "ledger_batch_id": pair.ledger_batch_id,
                "ledger_file_name": files[pair.ledger_batch_id][0],
                "ledger_file_sha256": files[pair.ledger_batch_id][1],
                "promotion_number": found.number, "promoted_by": found.promoted_by,
                "promoted_at": found.promoted_at,
                "breaks": {severity: severities.get(severity, 0) for severity in SEVERITIES},
                "acknowledged": len(ids & set(latest)),
                **{action: actions.get(action, 0) for action in promotion.ACTIONS},
                **view, **tail}

    # -- Closing ---------------------------------------------------------------------------------

    def close(self) -> None:
        """Closes the connection once no query runs, and removes the session's folder."""
        if self.closed:
            return
        with self._lock.write():
            if self.closed:
                return
            self.closed = True
            self._con.close()
        shutil.rmtree(self.folder, ignore_errors=True)
        _LIVE.discard(self)
        if self._on_close is not None:
            self._on_close(self)


def remove_stale(root: Path | str, now: Optional[float] = None) -> list:
    """Removes the folders under root with the session prefix that are older than one day."""
    now = time.time() if now is None else now
    removed = []
    for folder in sorted(Path(root).glob(f"{PREFIX}*")):
        if folder.is_dir() and now - folder.stat().st_mtime > STALE_SECONDS:
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder)
    return removed


class Sessions:
    """The open sessions of one template, at most max_sessions of them."""

    def __init__(self, template: Template, *, root: Path | str | None = None,
                 max_sessions: Optional[int] = None):
        self.template = template
        self.root = Path(root) if root is not None else Path(tempfile.gettempdir())
        self.max_sessions = config.MAX_SESSIONS if max_sessions is None else max_sessions
        if self.max_sessions < 1:
            raise ValueError("a pool holds at least one session")
        self._open: OrderedDict = OrderedDict()      # least recently used first
        self._guard = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        remove_stale(self.root)

    def __len__(self) -> int:
        return len(self._open)

    def open(self) -> Session:
        """A new session; when the pool is full, the least recently used one is closed first."""
        with self._guard:
            evicted = []
            while len(self._open) >= self.max_sessions:
                oldest, _ = self._open.popitem(last=False)
                evicted.append(oldest)
        for session in evicted:
            session.close()
        folder = Path(tempfile.mkdtemp(prefix=PREFIX, dir=self.root))
        try:
            session = Session(self.template, folder, on_use=self._used, on_close=self._closed)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        with self._guard:
            self._open[session] = None
        return session

    def _used(self, session: Session) -> None:
        with self._guard:
            if session in self._open:
                self._open.move_to_end(session)

    def _closed(self, session: Session) -> None:
        with self._guard:
            self._open.pop(session, None)

    def close_all(self) -> None:
        for session in list(self._open):
            session.close()


@atexit.register
def _close_every_session() -> None:
    """At exit, every session still open is closed and its folder removed."""
    for session in list(_LIVE):
        session.close()
