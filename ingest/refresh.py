"""`make ingest` - refresh the raw mirror from the live sources.

Thin by design. Every decision this module could get wrong has been pushed
into a pure, tested module: what a document says is `parse`, what to download
is `catalog`, which districts matter is `districts`, how projects are grouped
is `projects`. What is left here is sequencing and reporting.

Incremental. The store remembers every document already considered, including
the ones it rejected, so a second run fetches only what is new and an
interrupted run resumes rather than restarting.

    python -m ingest.refresh                      # everything from 2018
    python -m ingest.refresh --since 2025         # only recent years
    python -m ingest.refresh --dry-run            # discover, download nothing
    python -m ingest.refresh --summarise-only     # republish the contract, no network
    python -m ingest.refresh --reparse            # re-read the mirror, no network
    python -m ingest.refresh --verify             # rehash the mirror, no network

Nothing here runs during `make build`. The build reads `data/raw/` and never
opens a socket.

**Interruption is the normal case, not the exception.** A full harvest is
roughly twenty thousand documents against a slow government host; it runs for
hours and it will be stopped. So the contract the build reads is republished
at checkpoints and on Ctrl-C, not once at the very end - previously an
interrupted run left the mirror full of new documents and the published
contract still describing the old one, with nothing saying so and a manual
`--summarise-only` as the only cure.

**Only one harvest may hold the store.** Two concurrent runs each kept the
whole decision map in memory and each wrote it whole, so the second to finish
silently erased the first's record while its files stayed on disk. A second
run now refuses to start and names the one already running.
"""
import argparse
import json
import os
import signal
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime

from . import config
from .bhoomirashi import gazetteer
from .districts import select
from .egazette import documents
from .egazette.catalog import fetch_priority, is_land_acquisition
from .egazette.parse import PARSER_VERSION, parse
from .egazette.records import Notification
from .egazette.search import MinistrySearch, SearchUnavailable
from .http import Fetcher, PermanentError, TransientError, httpx_transport
from .projects import assemble
from .store import (
    KEPT,
    REJECTED,
    UNAVAILABLE,
    UNREADABLE,
    Lock,
    LockHeld,
    RawStore,
    new_run_id,
    sha256_hex,
    write_atomic,
    write_json_atomic,
)
from .tls import ca_bundle

# The two files the offline build reads. Everything else under data/raw/ is
# the mirror these are derived from.
PROJECTS = os.path.join(config.RAW, "acquisition_projects.json")
TARGET_DISTRICTS = os.path.join(config.RAW, "target_districts.json")

# Republish the contract this often during a harvest. Cheap - it is pure file
# IO over the mirror - and it is what makes "resumable" true of the published
# contract and not only of the mirror.
CHECKPOINT_EVERY = 200

# Bytes that are not a readable PDF are usually a download this host
# truncated, so they are retried. After this many attempts they are not: at
# some point the honest reading is that the document really is unreadable,
# and a multi-megabyte fetch repeated on every harvest for ever is a cost
# borne by someone else's public infrastructure.
MAX_UNREADABLE_ATTEMPTS = 3


class _Interrupted(Exception):
    """Ctrl-C, or a SIGTERM. Unwinds to the publish-and-report path rather
    than out of the process, so the work already done is not stranded."""


def refresh(since=config.EARLIEST_YEAR, until=None, dry_run=False, limit=None,
            summarise_only=False, reparse=False, verify=False,
            recheck_rejected=False, force_unlock=False, log=print):
    """Harvest, parse and publish. Returns a report dict.

    `summarise_only` republishes the contract from whatever the mirror
    already holds, without opening a socket. A full harvest runs for hours
    and will be interrupted; without this, an interrupted run would leave the
    mirror full and the build with nothing to read.
    """
    resolved_until = until or date.today().year
    run_id = new_run_id()
    store = RawStore(config.RAW, run_id=run_id)

    # The offline paths touch no network and cannot race a harvest for the
    # source's attention, but they do write, so they take the lock too.
    with Lock(os.path.join(config.RAW, ".lock"), run_id).acquire(force=force_unlock):
        if verify:
            return _verify(store, log)
        if recheck_rejected:
            return _recheck_rejected(store, log)
        if reparse:
            outcome = _reparse(store, log)
            return _summarise(store, log, harvested=0, skipped=0, failed=0, **outcome)
        if summarise_only:
            return _summarise(store, log, harvested=0, skipped=0, failed=0)

        bundle = ca_bundle(config.EGAZETTE_HOST, config.CACHE)
        if bundle is None:
            log("could not repair the e-Gazette certificate chain; "
                "skipping the gazette harvest")
            return _summarise(store, log, harvested=0, skipped=0, failed=0)

        fetcher = Fetcher(httpx_transport(verify=bundle),
                          min_interval=config.MIN_REQUEST_INTERVAL,
                          retries=config.MAX_RETRIES)

        status = "completed"
        counts = {"harvested": 0, "skipped": 0, "failed": 0}
        with _interruptible(log):
            try:
                counts = _harvest(fetcher, store, since, resolved_until,
                                  dry_run, limit, log)
            except _Interrupted:
                status = "interrupted"
                counts = _harvest.last_counts
            else:
                _refresh_gazetteer(fetcher, log)

        report = _summarise(store, log, **counts)
        _write_run_manifest(store, log, status=status, resolved_until=resolved_until,
                            args={"since": since, "until": until, "limit": limit,
                                  "dry_run": dry_run},
                            report=report)
        return report


def _interruptible(log):
    """Turn the first Ctrl-C into an exception, and leave the second alone.

    A handler that published the contract itself would be doing file IO
    inside a signal handler; raising instead lets the harvest unwind to the
    ordinary publish path. Restoring the default handler means an operator
    who really wants the process gone can press Ctrl-C again and get it.
    """

    class _Guard:
        def __enter__(self):
            self._previous = {}
            for name in ("SIGINT", "SIGTERM"):
                number = getattr(signal, name, None)
                if number is None:
                    continue
                try:
                    self._previous[number] = signal.signal(number, self._raise)
                except (ValueError, OSError):
                    pass       # not the main thread; nothing to install
            return self

        def _raise(self, _number, _frame):
            self.__exit__()
            log("\n  interrupted - publishing what has been harvested so far "
                "(press again to stop immediately)")
            raise _Interrupted

        def __exit__(self, *_):
            for number, previous in self._previous.items():
                try:
                    signal.signal(number, previous)
                except (ValueError, OSError):
                    pass
            self._previous = {}
            return False

    return _Guard()


def _harvest(fetcher, store, since, until, dry_run, limit, log):
    harvested = skipped = failed = 0
    since_checkpoint = 0
    search = MinistrySearch(fetcher)

    def counts():
        return {"harvested": harvested, "skipped": skipped, "failed": failed}

    # Kept up to date as the harvest runs, so the interrupted path can report
    # what had been done by the time the signal arrived.
    _harvest.last_counts = counts()

    # Newest first. Bhoomi Rashi coverage improves over time, and a run that
    # is stopped early should have collected the most complete years.
    for year in range(until, since - 1, -1):
        for month in range(12, 0, -1):
            for ministry, code in config.MINISTRIES.items():
                try:
                    entries = list(search.month(code, year, month))
                except (SearchUnavailable, TransientError, PermanentError) as exc:
                    # Discovery is the fragile half. A month that cannot be
                    # listed is a gap in this run, not a failed build - the
                    # store keeps everything already harvested, and the next
                    # run will try the month again.
                    log(f"  {ministry} {year}-{month:02d}: search unavailable ({exc})")
                    continue

                wanted = sorted(
                    (e for e in entries
                     if is_land_acquisition(e.subject) and store.should_fetch(e.doc_id)),
                    key=lambda e: fetch_priority(e.subject))
                if entries:
                    log(f"  {ministry} {year}-{month:02d}: {len(entries)} listed, "
                        f"{len(wanted)} new to fetch")
                if dry_run:
                    continue

                for entry in wanted:
                    if limit is not None and harvested >= limit:
                        log(f"  reached the limit of {limit} documents")
                        _harvest.last_counts = counts()
                        return counts()
                    outcome = _ingest_one(store, fetcher, entry)
                    harvested += outcome == "kept"
                    skipped += outcome == "skipped"
                    failed += outcome == "failed"
                    _harvest.last_counts = counts()

                    since_checkpoint += 1
                    if since_checkpoint >= CHECKPOINT_EVERY:
                        since_checkpoint = 0
                        _checkpoint(store, log, counts())
    return counts()


_harvest.last_counts = {"harvested": 0, "skipped": 0, "failed": 0}


def _checkpoint(store, log, counts):
    """Republish the contract mid-harvest."""
    report = _summarise(store, lambda *_: None, **counts)
    log(f"  [checkpoint] {report['notifications_on_disk']} notifications on disk "
        f"-> {report['projects']} projects published")


def _ingest_one(store, fetcher, entry):
    """Download, parse and file one notification.

    Every outcome is recorded, including rejection, so the next run does not
    re-decide it and a small yield stays explainable from `index.json`.

    The order is load-bearing: bytes, then text, then the decision. Each
    artefact is durable before anything claims it exists, so a crash at any
    point leaves the document eligible to be fetched again rather than
    recorded as handled when it is not.
    """
    try:
        pdf = documents.fetch(fetcher, entry.doc_id, entry.year)
    except PermanentError as exc:
        store.record(entry.doc_id, outcome=UNAVAILABLE, year=entry.year,
                     reason=f"unavailable: {exc}")
        return "skipped"
    except TransientError as exc:
        # Never a decision: a transient failure must stay eligible for the
        # next run, or one bad afternoon permanently loses a project. It is
        # counted now, so a document that fails on every run is visible.
        store.defer(entry.doc_id, reason=str(exc))
        return "failed"

    content_sha256 = sha256_hex(pdf)
    text, why = documents.extract(pdf)
    if text is None:
        return _unreadable(store, entry, content_sha256, why)

    notification = parse(text, doc_id=entry.doc_id)
    keep = notification is not None and notification.section in ("3A", "3D")

    # Before the decision, and on the rejection path too. This is the whole
    # reason a rejection can be revisited later: `parse` is a pure function
    # of this text, so an improved parser can re-decide it offline instead of
    # being blocked by an index that says "decided" over bytes that are gone.
    text_sha256 = store.put_text(entry.doc_id, text, evidence=not keep)

    if not keep:
        # The catalog's free-text subject said land acquisition; the
        # document's own operative clause disagrees, and it wins.
        reason = "not an acquisition notification"
        if notification is not None:
            reason = f"section {notification.section}, no acquisition clock"
        store.record(entry.doc_id, outcome=REJECTED, year=entry.year, reason=reason,
                     content_sha256=content_sha256, text_sha256=text_sha256,
                     parser_version=PARSER_VERSION)
        return "skipped"

    store.put_document(entry.doc_id, entry.year, pdf)
    store.put_notification(entry.doc_id, asdict(notification))
    store.record(entry.doc_id, outcome=KEPT, year=entry.year,
                 content_sha256=content_sha256, text_sha256=text_sha256,
                 parser_version=PARSER_VERSION)
    return "kept"


def _unreadable(store, entry, content_sha256, why):
    """Bytes that arrived but yielded no text.

    A PDF that opens and simply has no text layer is a scan: settled at once,
    because re-downloading it can only produce the same scan. Bytes that are
    not a PDF at all are usually a truncated response from a slow host, so
    they are retried - but only a bounded number of times, because the two
    cases are not always distinguishable and an unbounded retry is an
    unbounded cost to the source.
    """
    if why == documents.NO_TEXT_LAYER:
        store.record(entry.doc_id, outcome=UNREADABLE, year=entry.year, reason=why,
                     content_sha256=content_sha256)
        return "skipped"

    attempts = store.attempts(entry.doc_id) + 1
    if attempts >= MAX_UNREADABLE_ATTEMPTS:
        store.record(entry.doc_id, outcome=UNREADABLE, year=entry.year,
                     reason=f"{why} after {attempts} attempts",
                     content_sha256=content_sha256)
        return "skipped"
    store.defer(entry.doc_id, reason=why)
    return "failed"


def _reparse(store, log):
    """Re-read the mirror through the current parser. No network.

    Needed whenever `parse.py` improves, which so far has been every time a
    new batch of real documents arrived. Re-downloading twenty thousand PDFs
    to correct a regex is not an option.

    Two things this now does that it could not before. It reads the retained
    *text* where there is any, so it works on a machine holding no PDFs at
    all - previously it reparsed nothing whatsoever on a fresh clone, because
    the PDFs are gitignored and it had no other source, and it reported that
    as success. And it reconsiders **rejections**, so an improved parser can
    pull a document back into the corpus instead of being locked out by a
    decision an older parser made.

    Idempotent: a document whose stored decision already names this parser
    version is skipped, so a second `--reparse` does nothing.
    """
    targets = store.stale(PARSER_VERSION)
    reparsed = recovered = dropped = unreachable = 0

    for doc_id in targets:
        decision = store.decision(doc_id) or {}
        was_kept = decision.get("outcome") == KEPT

        text = store.read_text(doc_id)
        if text is None:
            pdf = store.read_document(doc_id)
            if pdf is None:
                unreachable += 1
                continue
            text = documents.to_text(pdf)
            if text is None:
                unreachable += 1
                continue
            store.put_text(doc_id, text, evidence=not was_kept)

        notification = parse(text, doc_id=doc_id)
        keep = notification is not None and notification.section in ("3A", "3D")
        text_sha256 = sha256_hex(text.encode("utf-8"))

        if keep:
            store.put_notification(doc_id, asdict(notification))
            store.record(doc_id, outcome=KEPT, year=decision.get("year"),
                         content_sha256=decision.get("content_sha256"),
                         text_sha256=text_sha256, parser_version=PARSER_VERSION)
            reparsed += 1
            if not was_kept:
                # The parser improved and this document belongs in the corpus
                # after all. Under the old index this was unreachable.
                store.put_text(doc_id, text, evidence=False)
                recovered += 1
        else:
            reason = "not an acquisition notification"
            if notification is not None:
                reason = f"section {notification.section}, no acquisition clock"
            store.record(doc_id, outcome=REJECTED, year=decision.get("year"),
                         reason=reason, content_sha256=decision.get("content_sha256"),
                         text_sha256=text_sha256, parser_version=PARSER_VERSION)
            if was_kept:
                _forget_notification(doc_id)
                dropped += 1

    store.flush_cache()
    log(f"  reparsed {reparsed} documents under parser {PARSER_VERSION}"
        + (f", recovered {recovered} previously rejected" if recovered else "")
        + (f", dropped {dropped} that no longer qualify" if dropped else ""))
    if unreachable:
        # The honest version of what used to be a silent no-op.
        log(f"  {unreachable} decided document(s) have neither retained text nor a "
            "mirrored PDF and cannot be reparsed offline")
    blocked = store.undecidable_rejections(PARSER_VERSION)
    if blocked:
        log(f"  {len(blocked)} rejection(s) predate the journal and kept no text; "
            '`make ingest ARGS="--recheck-rejected"` re-fetches them: '
            + ", ".join(str(d) for d in blocked[:8])
            + ("..." if len(blocked) > 8 else ""))
    return {"reparsed": reparsed, "recovered": recovered, "dropped": dropped}


def _forget_notification(doc_id):
    """A document the current parser says does not belong in the corpus.

    Removed rather than left behind: `read_notifications` composes the
    contract from whatever files are present, so a stale one would keep
    appearing in the published projects after the decision that put it there
    had been overturned.
    """
    path = os.path.join(config.RAW, "egazette", "notifications", f"{doc_id}.json")
    try:
        os.unlink(path)
    except OSError:
        pass


def _recheck_rejected(store, log):
    """Retract rejections that cannot be revisited offline, so the next
    harvest fetches them again.

    The only recovery path for decisions made before any text was retained.
    Explicit, and an operator action, because it spends real requests on the
    source.
    """
    blocked = store.undecidable_rejections(PARSER_VERSION)
    for doc_id in blocked:
        store.reopen(doc_id, reason=f"no retained text; parser now {PARSER_VERSION}")
    store.flush_cache()
    log(f"[ingest] reopened {len(blocked)} rejection(s) with no retained text; "
        "the next harvest will fetch them again")
    return {"reopened": len(blocked), "doc_ids": blocked}


def _verify(store, log):
    """Rehash the mirror against the journal. No network."""
    report = store.verify()
    log(f"[ingest] verified {report['checked']} kept documents")
    problems = 0
    for label, key in (("changed since they were mirrored", "documents_changed"),
                       ("whose retained text changed", "text_changed"),
                       ("with no notification on disk", "missing_notifications"),
                       ("that will not parse", "damaged_notifications")):
        bad = report[key]
        problems += len(bad)
        if bad:
            log(f"  {len(bad)} {label}: "
                + ", ".join(str(d) for d in bad[:8]) + ("..." if len(bad) > 8 else ""))
    if not problems:
        log("  no discrepancies")
    return report


def _refresh_gazetteer(fetcher, log):
    """Bhoomi Rashi's official district ids for the target states.

    Not required to build the corpus - it is the vocabulary for reconciling a
    district name read out of a gazette PDF with the district the Ministry's
    own portal recognises. A failure here is worth reporting and no more.
    """
    out = {}
    for state, meta in config.TARGET_STATES.items():
        try:
            out[state] = gazetteer.districts(fetcher, meta["bhoomirashi_state_id"])
        except (TransientError, PermanentError) as exc:
            log(f"  gazetteer for {state} unavailable ({exc})")
    if out:
        _write_json(os.path.join(config.RAW, "bhoomirashi_districts.json"), out)


def _summarise(store, log, harvested, skipped, failed, reparsed=0, recovered=0,
               dropped=0):
    """Write the two files the pipeline reads.

    The per-document JSON under `notifications/` is the mirror; these two are
    the *contract*, in the same way `data/input/*.parquet` is the contract
    between s0 and s1. Publishing them means the pipeline needs one well-known
    path and no knowledge of how the mirror is laid out - the dependency
    stays a file, not an import.
    """
    notifications = store.read_notifications()
    projects = assemble(_rehydrate(n) for n in notifications)
    contract = _write_json(PROJECTS, [_projectable(p) for p in projects])
    chosen = select(notifications, config.DISTRICTS_PER_STATE,
                    config.ALWAYS_INCLUDE_DISTRICTS)
    _write_json(TARGET_DISTRICTS, chosen)
    store.flush_cache()
    _publish_manifest(store, contract, len(notifications), len(projects))

    report = {
        "harvested": harvested, "skipped": skipped, "failed": failed,
        "reparsed": reparsed, "recovered": recovered, "dropped": dropped,
        "notifications_on_disk": len(notifications),
        "declarations": sum(1 for n in notifications if n.get("section") == "3D"),
        "intentions": sum(1 for n in notifications if n.get("section") == "3A"),
        "projects": len(projects),
        "closed_projects": sum(1 for p in projects if p.declared_3d_on),
        "target_districts": chosen,
        "corpus_sha256": store.corpus_fingerprint(),
        "parser_version": PARSER_VERSION,
        "damaged_notifications": list(store.damaged),
    }
    log(f"[ingest] {report['notifications_on_disk']} notifications on disk "
        f"({report['declarations']} x 3D, {report['intentions']} x 3A) "
        f"-> {report['projects']} projects, {report['closed_projects']} with a real "
        f"3A->3D interval; +{harvested} kept, {skipped} skipped, {failed} failed this run")
    if store.damaged:
        # Skipped rather than fatal, but never silent: a file that will not
        # parse is a document missing from the contract.
        log(f"  {len(store.damaged)} notification file(s) unreadable and skipped: "
            + ", ".join(store.damaged[:8]) + ("..." if len(store.damaged) > 8 else ""))
    for state, names in chosen.items():
        log(f"  {state}: {', '.join(names)}")
    return report


def _publish_manifest(store, contract_bytes, notifications, projects):
    """A fingerprint beside the contract, never inside it.

    A sidecar rather than extra keys, so the contract's own shape is exactly
    what it was and the pipeline that reads it needs no change at all. It is
    what lets a build state which harvest produced its figures.

    Rewritten only when something it describes actually changed. Stamping a
    fresh timestamp on every no-op republish would make repeated runs produce
    different bytes, which is precisely the property this whole change exists
    to establish.
    """
    path = os.path.splitext(PROJECTS)[0] + ".manifest.json"
    manifest = {
        "contract_sha256": sha256_hex(contract_bytes),
        "corpus_sha256": store.corpus_fingerprint(),
        "parser_version": PARSER_VERSION,
        "notifications": notifications,
        "projects": projects,
    }
    try:
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)
    except (OSError, ValueError):
        existing = {}
    if {key: existing.get(key) for key in manifest} == manifest:
        return
    write_json_atomic(path, dict(manifest, run_id=store.run_id, published_at=_now()),
                      sort_keys=True)


def _write_run_manifest(store, log, status, resolved_until, args, report):
    """What this run attempted and what it achieved.

    `resolved_until` is the point: the scope defaults to `date.today().year`,
    so the same command run on either side of 1 January covers different
    years. Without recording it, a gap in the mirror is indistinguishable
    from a year that was never in scope.
    """
    path = os.path.join(config.RAW, "egazette", "runs", store.run_id, "manifest.json")
    write_json_atomic(path, {
        "run_id": store.run_id,
        "finished_at": _now(),
        "status": status,
        "args": args,
        "resolved_until": resolved_until,
        "code": {"parser_version": PARSER_VERSION,
                 "extractor": _extractor_version(),
                 "python": sys.version.split()[0]},
        "config_sha256": _config_fingerprint(),
        "counts": {key: report[key] for key in ("harvested", "skipped", "failed")},
        "corpus_sha256": report["corpus_sha256"],
    }, sort_keys=True)
    log(f"  run {store.run_id} {status}; manifest at "
        + os.path.relpath(path, config.ROOT))


def _config_fingerprint():
    """What was in scope, as a value. A harvest that found nothing because
    the ministry codes changed should not look like a harvest that found
    nothing because there was nothing there."""
    return sha256_hex(json.dumps({
        "ministries": config.MINISTRIES,
        "part_section": config.PART_SECTION,
        "earliest_year": config.EARLIEST_YEAR,
        "document_url": config.EGAZETTE_DOCUMENT,
    }, sort_keys=True).encode("utf-8"))


def _extractor_version():
    try:
        from importlib.metadata import PackageNotFoundError, version

        return f"pdfplumber {version('pdfplumber')}"
    except (ImportError, PackageNotFoundError):
        return "pdfplumber (version unknown)"


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _rehydrate(record):
    """A stored notification back into a `Notification`.

    Dates round-trip through the JSON as ISO strings; everything downstream
    of `assemble` compares and subtracts them, so they have to come back as
    dates rather than as strings that happen to sort correctly.

    Underscore-prefixed keys are annotations rather than fields of the
    record, and are dropped: `Notification` takes exactly its own fields, so
    anything else reaches it as an unexpected keyword.
    """
    fields = {k: v for k, v in record.items() if not k.startswith("_")}
    for name in ("notified_on", "parent_notified_on"):
        if fields.get(name):
            fields[name] = date.fromisoformat(str(fields[name])[:10])
    for name in ("districts", "villages"):
        fields[name] = tuple(fields.get(name) or ())
    return Notification(**fields)


def _projectable(project):
    row = asdict(project)
    row["notified_3a_on"] = project.notified_3a_on.isoformat()
    row["declared_3d_on"] = (project.declared_3d_on.isoformat()
                             if project.declared_3d_on else None)
    return row


def _write_json(path, payload):
    """Serialise once, hash what was written, and replace the file atomically.

    Atomically because `make build` reads `acquisition_projects.json` and a
    harvest rewrites it: truncating it in place meant a build could read a
    half-written contract, and an interruption could leave one on disk.

    A republish that would produce the identical bytes does not touch the
    file at all. Rewriting it would be harmless to its content but not to
    what the content *means* to anything watching: a build keyed on
    modification time would rebuild, and an operator running
    `--summarise-only` to check the mirror would be told the contract had
    changed when nothing had.
    """
    body = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
    encoded = body.encode("utf-8")
    try:
        with open(path, "rb") as fh:
            if fh.read() == encoded:
                return encoded
    except OSError:
        pass
    write_atomic(path, encoded)
    return encoded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", type=int, default=config.EARLIEST_YEAR,
                        help=f"earliest publication year (default {config.EARLIEST_YEAR})")
    parser.add_argument("--until", type=int, default=None, help="latest year (default: this year)")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be fetched, download nothing")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after this many documents; the run is resumable")
    parser.add_argument("--summarise-only", action="store_true",
                        help="republish the contract from the existing mirror, no network")
    parser.add_argument("--reparse", action="store_true",
                        help="re-read the mirror through the current parser, no network")
    parser.add_argument("--verify", action="store_true",
                        help="rehash the mirror against the journal, no network")
    parser.add_argument("--recheck-rejected", action="store_true",
                        help="retract rejections that kept no text, so they are fetched again")
    parser.add_argument("--force-unlock", action="store_true",
                        help="take the store lock from a run that is no longer running")
    args = parser.parse_args(argv)
    try:
        refresh(since=args.since, until=args.until, dry_run=args.dry_run, limit=args.limit,
                summarise_only=args.summarise_only, reparse=args.reparse, verify=args.verify,
                recheck_rejected=args.recheck_rejected, force_unlock=args.force_unlock)
    except LockHeld as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
