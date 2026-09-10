"""The on-disk mirror of everything harvested from the network.

`data/raw/` is the boundary between the network and the build. The ingest
layer only ever writes here; the offline build only ever reads here. That is
what makes a rebuild deterministic and lets the demo run with no government
server reachable.

The store also remembers what it has already *looked at*, including documents
it decided not to keep, so a refresh never re-downloads a notification it
already rejected and an interrupted harvest resumes instead of restarting.
Government hosts are slow enough that this is a practical requirement, not a
nicety.

Deliberately not a database. The unit of storage is a file named after the
document it came from, so the mirror can be inspected, diffed and pruned with
ordinary tools, and "what do we have?" is answerable without running code.

Three properties make that safe to rely on, and each replaced something that
used to be able to lose data:

**Every write is atomic.** `write_atomic` writes to a temporary file in the
same directory, fsyncs it, and `os.replace`s it into position - atomic on
POSIX and Windows alike. Before this, every write here truncated its target
in place, so an interruption mid-write left a half-written JSON file; a
truncated `index.json` then raised `JSONDecodeError` inside this class's own
constructor and took down *every* entry point, including the offline recovery
paths that exist precisely for that situation.

**Decisions are appended, never rewritten.** The record of what has been
considered is an append-only NDJSON journal. Appending is O(1) rather than a
full rewrite per document - the old index was re-serialised in its entirety
once per decision, which is quadratic over a twenty-thousand-document harvest
- and a crash can only ever damage the final line, which is discarded on
read. `index.json` still exists and still has its old shape, but it is now a
*derived cache*: if it is missing, stale or corrupt it is rebuilt from the
journal rather than repaired.

**A decision names the parser that made it.** `kept: true/false` could not
express *why* something was rejected or *under which* parser, so a rejection
was permanent and its evidence was discarded. Outcomes are now drawn from a
closed vocabulary, carry the hash of the bytes they were made from, and - for
the revisable ones - keep the extracted text, so improving `parse.py` can
recover documents instead of losing them for good.
"""
import gzip
import hashlib
import json
import os
import socket
import tempfile
from datetime import UTC, datetime

# ---- the outcome vocabulary -------------------------------------------------

# A document is either decided or it is not. Splitting "not kept" into its
# real causes is what stops an unreadable PDF being re-downloaded on every
# harvest forever: `failed` used to cover both "the connection dropped" and
# "these bytes are not a readable PDF", and only the first should be retried.
KEPT = "kept"                  # parsed as a 3A/3D and mirrored
REJECTED = "rejected"          # fetched and parsed; not an acquisition notification
UNAVAILABLE = "unavailable"    # the server does not have it (404/410)
UNREADABLE = "unreadable"      # fetched, but no text could be extracted
DEFERRED = "deferred"          # transient failure; stays eligible, never a decision
REOPENED = "reopened"          # an operator retracted a decision; eligible again

OUTCOMES = (KEPT, REJECTED, UNAVAILABLE, UNREADABLE, DEFERRED, REOPENED)

# Outcomes that settle a document. Anything else leaves it eligible for the
# next run, which is what makes a transient failure survive one bad afternoon.
TERMINAL = frozenset({KEPT, REJECTED, UNAVAILABLE, UNREADABLE})

# Decisions a better parser could legitimately overturn. A `kept` document is
# re-parsed too, but re-parsing it cannot change whether it belongs in the
# corpus; these two can.
REVISABLE = frozenset({REJECTED, UNREADABLE})

# The journal a pre-journal `index.json` is imported into on first open, named
# so it sorts before every real run id.
LEGACY_RUN_ID = "00000000T000000Z-legacy"

# The derived cache is rewritten at most this often. It is disposable, so
# there is nothing to protect by writing it more; the journal is what has
# already been made durable by the time this matters.
_CACHE_EVERY = 256


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def new_run_id(now=None, nonce=None):
    """A sortable identifier for one harvest.

    Timestamp first so journals sort chronologically by filename, plus a
    nonce so two runs started in the same second cannot collide.
    """
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{nonce or os.urandom(3).hex()}"


def write_atomic(path, payload):
    """Write `payload` so that readers see either the old file or the new one.

    The temporary file is created in the *destination directory*, because
    `os.replace` is only atomic within a filesystem and the system temp
    directory is routinely on a different one.
    """
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        _unlink(tmp)
        raise


def write_json_atomic(path, payload, **dumps):
    dumps.setdefault("ensure_ascii", False)
    dumps.setdefault("indent", 1)
    write_atomic(path, json.dumps(payload, **dumps))


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _write_if_changed(path, payload, stamp=None):
    """Write `payload` only if it differs from what is already there.

    `stamp`, when given, names a timestamp field added on the way out and
    ignored on the way in - so "when was this last rebuilt" can be recorded
    without that answer being the reason the file changed.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)
    except (OSError, ValueError):
        existing = None
    if isinstance(existing, dict) and stamp:
        existing = {k: v for k, v in existing.items() if k != stamp}
    if existing == payload:
        return False
    if stamp:
        payload = dict(payload)
        payload[stamp] = datetime.now(UTC).isoformat(timespec="seconds")
    write_json_atomic(path, payload, sort_keys=True)
    return True


class LockHeld(RuntimeError):
    """Another harvest holds the store. Carries whatever the holder recorded
    about itself, so the message can name it rather than just refusing."""

    def __init__(self, path, holder):
        self.path = path
        self.holder = holder or {}
        who = ", ".join(f"{k}={v}" for k, v in sorted(self.holder.items())) or "unknown"
        super().__init__(
            f"another harvest holds {path} ({who}). "
            "If it is not running, re-run with --force-unlock.")


class Lock:
    """A single-writer lock over the store directory.

    Deliberately *not* expiring by age. A lock that releases itself after N
    minutes is a lock that lets a second writer in exactly when the first one
    is slow, which is when the damage is worst - and the damage here is
    silent: two harvests each keep the whole decision map in memory, and the
    last one to write erases the other's work while its files stay on disk.
    Clearing a stale lock is a deliberate operator action.
    """

    def __init__(self, path, run_id):
        self._path = str(path)
        self._run_id = run_id
        self._held = False

    def holder(self):
        try:
            with open(self._path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def acquire(self, force=False):
        try:
            self._claim()
        except FileExistsError:
            if not force:
                raise LockHeld(self._path, self.holder()) from None
            _unlink(self._path)
            self._claim()
        self._held = True
        return self

    def _claim(self):
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        handle = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(),
                       "run_id": self._run_id,
                       "started_at": datetime.now(UTC).isoformat(timespec="seconds")},
                      fh, indent=1)

    def release(self):
        if self._held:
            _unlink(self._path)
            self._held = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()


class RawStore:
    """Documents, extracted text, parsed notifications, and the record of what
    has been considered."""

    def __init__(self, root, run_id=None):
        self._root = str(root)
        self._base = os.path.join(self._root, "egazette")
        self._index_path = os.path.join(self._base, "index.json")
        self._meta_path = os.path.join(self._base, "index.meta.json")
        # One log, not one per run. Append order is then the total order, and
        # a later decision is later because it was written later. Splitting
        # the journal by run made ordering depend on sorting run ids, which
        # are only accurate to the second - two runs inside the same second
        # sorted by their random nonce, so an older decision could win.
        self._journal_path = os.path.join(self._base, "journal.ndjson")
        self._notifications = os.path.join(self._base, "notifications")
        # Source PDFs are megabytes each and stay out of git; the parsed
        # notifications beside them are kilobytes and are committed, so the
        # build needs no network. One prefix keeps that split to one
        # .gitignore line.
        self._pdf = os.path.join(self._base, "pdf")
        # Extracted text, split by whether it is evidence for a decision that
        # might be overturned. `rejected/` is committed - it is what lets an
        # improved parser revisit a rejection with no network and no PDF -
        # while `text/` is a local convenience for the kept corpus, whose
        # parsed JSON is committed already.
        self._text = os.path.join(self._base, "text")
        self._rejected_text = os.path.join(self._base, "rejected")

        self.run_id = run_id or new_run_id()
        self.damaged = []          # notification files that would not parse
        self._since_cache = 0
        self._cache_written = False

        self._migrate_legacy_index()
        self._index = self._project()

    # ---- source documents --------------------------------------------------

    def put_document(self, doc_id, year, content):
        """Mirror one gazette PDF, partitioned by publication year exactly as
        the source partitions it.

        A document already held under a *different* year is removed rather
        than left beside the new copy. `_find_document` resolves a bare
        document id by scanning years in order, so two copies would make
        every later read silently return whichever year sorted first.
        """
        path = self._document_path(doc_id, year)
        for stale in self._document_paths(doc_id):
            if stale != path:
                _unlink(stale)
        write_atomic(path, content)
        return sha256_hex(content)

    def read_document(self, doc_id):
        path = self._find_document(doc_id)
        if path is None:
            return None
        with open(path, "rb") as fh:
            return fh.read()

    def holds(self, doc_id):
        """Whether the source document itself is on disk."""
        return self._find_document(doc_id) is not None

    # ---- extracted text ----------------------------------------------------

    def put_text(self, doc_id, text, *, evidence=False):
        """Keep the text a decision was made from.

        Stored for every fetched document, including the rejected ones. The
        rejection path is precisely the one whose evidence gets wanted again:
        `parse.py` has been corrected on every new batch of real documents so
        far, and without the text a correction cannot reach anything already
        turned away - the bytes are gone and the index says "decided".

        Text rather than the PDF because `parse` is a pure function of the
        text: it is two orders of magnitude smaller, and it is the actual
        input, so re-deciding from it cannot drift from what the harvest saw.
        """
        payload = gzip.compress(text.encode("utf-8"), mtime=0)
        write_atomic(self._text_path(doc_id, evidence=evidence), payload)
        return sha256_hex(text.encode("utf-8"))

    def read_text(self, doc_id):
        for path in (self._text_path(doc_id), self._text_path(doc_id, evidence=True)):
            if os.path.exists(path):
                try:
                    with gzip.open(path, "rb") as fh:
                        return fh.read().decode("utf-8")
                except (OSError, EOFError, gzip.BadGzipFile):
                    return None
        return None

    def has_text(self, doc_id):
        return any(os.path.exists(p) for p in
                   (self._text_path(doc_id), self._text_path(doc_id, evidence=True)))

    # ---- parsed notifications ----------------------------------------------

    def put_notification(self, doc_id, record):
        write_json_atomic(self._notification_path(doc_id), record, default=str)

    def read_notifications(self):
        """Every parsed notification, in document-id order.

        Stable ordering is what lets the build downstream be deterministic:
        `os.listdir` is not sorted, so leaving it unsorted would make the
        composed corpus depend on filesystem iteration order.

        A file that will not parse is skipped and remembered on `.damaged`
        rather than raised. One half-written file used to make this method -
        and therefore the published contract, and therefore the build - fail,
        holding a mirror that was otherwise complete hostage to a single
        interrupted write.
        """
        self.damaged = []
        if not os.path.isdir(self._notifications):
            return []
        out = []
        for name in sorted(os.listdir(self._notifications), key=_document_id_of):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self._notifications, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    out.append(json.load(fh))
            except (OSError, ValueError):
                self.damaged.append(name)
        return out

    # ---- what has already been considered ----------------------------------

    def record(self, doc_id, kept=None, reason=None, *, outcome=None, year=None,
               content_sha256=None, text_sha256=None, parser_version=None):
        """Note that this document has been handled, and why.

        `reason` is what makes a small harvest auditable: a yield that drops
        is explainable from the index rather than being a mystery.

        `kept=` is kept as the legacy spelling of `outcome=`, so every
        existing caller and test keeps working unchanged.
        """
        if outcome is None:
            outcome = KEPT if kept else REJECTED
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome: {outcome!r}")
        self._append({
            "run_id": self.run_id,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "doc_id": _as_doc_id(doc_id),
            "outcome": outcome,
            "year": year,
            "content_sha256": content_sha256,
            "text_sha256": text_sha256,
            "parser_version": parser_version,
            "reason": reason,
        })

    def defer(self, doc_id, reason=None):
        """A transient failure. Counted, never decided.

        Recording the attempt is new; leaving the document eligible is not.
        A document that fails on every single run used to be invisible - the
        harvest reported a number and nothing said which documents it was.
        """
        self.record(doc_id, outcome=DEFERRED, reason=reason)

    def reopen(self, doc_id, reason=None):
        """Retract a decision so the document is fetched again.

        Append-only, so the retraction is itself part of the audit trail
        rather than an edit that erases what was decided before.
        """
        self.record(doc_id, outcome=REOPENED, reason=reason)

    def has_seen(self, doc_id):
        return self.decision(doc_id) is not None

    def kept(self, doc_id):
        state = self.decision(doc_id) or {}
        return state.get("outcome") == KEPT

    def decision(self, doc_id):
        """The settled decision for a document, or None if it has none.

        A document that has only ever failed transiently has a *record* here
        but no decision, which is what keeps it eligible.
        """
        state = self._index.get(str(_as_doc_id(doc_id)))
        if state and state.get("outcome") in TERMINAL:
            return state
        return None

    def attempts(self, doc_id):
        return int(self._index.get(str(_as_doc_id(doc_id)), {}).get("attempts", 0))

    def should_fetch(self, doc_id):
        """Whether this document is still worth spending a request on.

        Deliberately unchanged in effect from the original `not has_seen(...)`:
        a settled document is never re-fetched, however far the parser has
        moved on. Re-deciding happens offline against retained text, and
        refetching is an explicit operator action - twenty thousand PDFs is
        not a cost to incur on someone else's public infrastructure by
        default.
        """
        return self.decision(doc_id) is None

    def stale(self, parser_version):
        """Decided documents that predate `parser_version` and can still be
        re-read without the network.

        `unavailable` is excluded because there are no bytes to re-read - the
        server never had the document. A revisable decision with no retained
        text is excluded too, and reported by `undecidable_rejections`
        instead: it is not stale work waiting to be done, it is work that
        cannot be done offline at all, and conflating the two is what made
        the old reparse look like it had succeeded when it had done nothing.
        """
        out = []
        for doc, state in self._index.items():
            outcome = state.get("outcome")
            if outcome not in TERMINAL or outcome == UNAVAILABLE:
                continue
            if state.get("parser_version") == parser_version:
                continue
            if outcome in REVISABLE and not self.has_text(doc):
                continue
            out.append(_as_doc_id(doc))
        return sorted(out, key=_doc_sort_key)

    def undecidable_rejections(self, parser_version):
        """Revisable decisions that cannot be revisited offline, because no
        text was retained when they were made.

        These are the pre-journal rejections. Naming them is the point: they
        are the only documents an improved parser genuinely cannot reach
        without going back to the network.
        """
        out = [_as_doc_id(doc) for doc, state in self._index.items()
               if (state.get("outcome") in REVISABLE
                   and state.get("parser_version") != parser_version
                   and not self.has_text(doc))]
        return sorted(out, key=_doc_sort_key)

    # ---- integrity ---------------------------------------------------------

    def corpus_fingerprint(self):
        """One hash over the whole kept corpus.

        Order-independent by the sort, and sensitive to exactly the three
        things that can change what the corpus means: which documents are in
        it, the bytes each one was read from, and the parser that read them.
        Two machines holding the same mirror compute the same value with no
        coordination, which is what lets a build state which harvest produced
        its figures.
        """
        lines = []
        for doc in sorted(self._index, key=_doc_sort_key):
            state = self._index[doc]
            if state.get("outcome") != KEPT:
                continue
            lines.append(f"{doc}\t{state.get('content_sha256') or ''}"
                         f"\t{state.get('parser_version') or ''}")
        return sha256_hex("\n".join(lines).encode("utf-8"))

    def verify(self):
        """Rehash what is on disk against what the journal says it should be.

        No network. Reports rather than repairs: the mirror is the record of
        a harvest, and quietly deleting part of it is not this function's
        decision to make.
        """
        report = {"checked": 0, "documents_changed": [], "text_changed": [],
                  "missing_notifications": [], "damaged_notifications": []}
        self.read_notifications()
        report["damaged_notifications"] = list(self.damaged)

        for doc in sorted(self._index, key=_doc_sort_key):
            state = self._index[doc]
            if state.get("outcome") != KEPT:
                continue
            report["checked"] += 1
            if not os.path.exists(self._notification_path(doc)):
                report["missing_notifications"].append(doc)
            expected = state.get("content_sha256")
            if expected and self.holds(doc):
                if sha256_hex(self.read_document(doc)) != expected:
                    report["documents_changed"].append(doc)
            expected_text = state.get("text_sha256")
            if expected_text and self.has_text(doc):
                text = self.read_text(doc)
                if text is None or sha256_hex(text.encode("utf-8")) != expected_text:
                    report["text_changed"].append(doc)
        return report

    # ---- the journal -------------------------------------------------------

    def _append(self, entry):
        os.makedirs(self._base, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
        # Append, flush, fsync: the decision is durable before the caller is
        # told it was made. A crash can only ever tear the final line, and a
        # torn final line is discarded when the journal is read back.
        with open(self._journal_path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        _apply(self._index, entry)
        self._since_cache += 1
        if not self._cache_written or self._since_cache >= _CACHE_EVERY:
            self.flush_cache()

    def flush_cache(self):
        """Rewrite the derived `index.json` (and its provenance) from the
        projection currently in memory.

        Disposable by design: if it is absent, stale or corrupt, `_project`
        rebuilds it from the journal. That is the whole reason the quadratic
        per-document rewrite could be dropped.

        Written only when the projection actually differs from what is on
        disk. A re-run that changes nothing must leave the store byte for
        byte as it found it - a cache that restamped itself every time would
        make `git status` report work after a run that did none, which is
        exactly the signal an idempotent harvest exists to give.
        """
        _write_if_changed(self._index_path, self._index)
        summary = {"entries": len(self._index),
                   "journals": self._journal_state(),
                   "corpus_sha256": self.corpus_fingerprint()}
        _write_if_changed(self._meta_path, summary, stamp="projected_at")
        self._since_cache = 0
        self._cache_written = True

    def _journal_state(self):
        try:
            with open(self._journal_path, "rb") as fh:
                body = fh.read()
        except OSError:
            return {}
        return {os.path.basename(self._journal_path):
                {"bytes": len(body), "sha256": sha256_hex(body)}}

    def _project(self):
        """Fold the journal, oldest entry first, into the decision map.

        Append order is chronological order, so a later decision supersedes
        an earlier one - the same "last write wins" the mutable index had,
        but derived from a record that cannot be left half-written.
        """
        index = {}
        try:
            with open(self._journal_path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return index
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                # A torn line. Only the last line can be torn by a crash
                # mid-append; anything else means the file was edited by
                # hand. Both are skipped, because a decision that cannot be
                # read is a decision that was never made.
                continue
            _apply(index, entry)
        return index

    def _migrate_legacy_index(self):
        """Import a pre-journal `index.json` exactly once.

        The mirror in this repository was harvested before the journal
        existed. Rebuilding the projection from an empty journal would
        silently discard every decision it holds - including the rejections,
        which are the ones that would then be re-downloaded.
        """
        if os.path.exists(self._journal_path) or not os.path.exists(self._index_path):
            return
        try:
            with open(self._index_path, encoding="utf-8") as fh:
                legacy = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(legacy, dict) or not legacy:
            return

        lines = []
        for doc in sorted(legacy, key=_doc_sort_key):
            state = legacy[doc] or {}
            outcome = state.get("outcome") or (KEPT if state.get("kept") else REJECTED)
            lines.append(json.dumps({
                "run_id": LEGACY_RUN_ID,
                "at": None,
                "doc_id": _as_doc_id(doc),
                "outcome": outcome,
                "year": state.get("year"),
                "content_sha256": state.get("content_sha256"),
                "text_sha256": state.get("text_sha256"),
                "parser_version": state.get("parser_version"),
                "reason": state.get("reason"),
            }, ensure_ascii=False, sort_keys=True))
        write_atomic(self._journal_path, "\n".join(lines) + "\n")

    # ---- internals ---------------------------------------------------------

    def _document_path(self, doc_id, year):
        return os.path.join(self._pdf, str(year), f"{doc_id}.pdf")

    def _document_paths(self, doc_id):
        """Every place this document is currently stored. Normally one."""
        if not os.path.isdir(self._pdf):
            return []
        found = []
        for year in sorted(os.listdir(self._pdf)):
            path = os.path.join(self._pdf, year, f"{doc_id}.pdf")
            if os.path.exists(path):
                found.append(path)
        return found

    def _find_document(self, doc_id):
        """Locate a document without being told its year.

        Callers reading the mirror back (the build, a re-parse) have a
        document id and nothing else; requiring them to carry the year too
        would push the storage layout into every caller.
        """
        paths = self._document_paths(doc_id)
        return paths[0] if paths else None

    def _notification_path(self, doc_id):
        return os.path.join(self._notifications, f"{doc_id}.json")

    def _text_path(self, doc_id, evidence=False):
        base = self._rejected_text if evidence else self._text
        return os.path.join(base, f"{doc_id}.txt.gz")


def _apply(index, entry):
    """Fold one journal entry into the projection."""
    doc = str(_as_doc_id(entry.get("doc_id")))
    if doc in ("None", ""):
        return
    outcome = entry.get("outcome")
    state = index.setdefault(doc, {"kept": False, "reason": None, "outcome": None,
                                   "attempts": 0})

    if outcome == DEFERRED:
        # Counted, but never a decision: a document that could not be reached
        # must stay eligible, or one bad afternoon loses a project for good.
        state["attempts"] = int(state.get("attempts", 0)) + 1
        state["last_deferred_reason"] = entry.get("reason")
        return

    if outcome == REOPENED:
        index[doc] = {"kept": False, "reason": entry.get("reason"), "outcome": None,
                      "attempts": int(state.get("attempts", 0)),
                      "reopened_at": entry.get("at")}
        return

    state.update({
        "kept": outcome == KEPT,
        "reason": entry.get("reason"),
        "outcome": outcome,
        "year": entry.get("year"),
        "content_sha256": entry.get("content_sha256"),
        "text_sha256": entry.get("text_sha256"),
        "parser_version": entry.get("parser_version"),
        "run_id": entry.get("run_id"),
        "at": entry.get("at"),
    })


def _as_doc_id(doc_id):
    """Document ids are integers in the source and arrive as either. One
    spelling in the journal, so a document cannot be decided twice under two
    types."""
    text = str(doc_id)
    return int(text) if text.isdigit() else text


def _doc_sort_key(doc):
    text = str(doc)
    return (0, int(text), "") if text.isdigit() else (1, 0, text)


def _document_id_of(filename):
    """Sort key: numeric where the name is a document id, so 265102 comes
    before 265154 rather than after it as a string comparison would have it
    for ids of differing length."""
    stem = filename.rsplit(".", 1)[0]
    return (0, int(stem)) if stem.isdigit() else (1, 0)
