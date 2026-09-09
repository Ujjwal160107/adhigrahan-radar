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

Nothing here runs during `make build`. The build reads `data/raw/` and never
opens a socket.
"""
import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date

from . import config
from .bhoomirashi import gazetteer
from .districts import select
from .egazette import documents
from .egazette.catalog import fetch_priority, is_land_acquisition
from .egazette.parse import parse
from .egazette.records import Notification
from .egazette.search import MinistrySearch, SearchUnavailable
from .http import Fetcher, PermanentError, TransientError, httpx_transport
from .projects import assemble
from .store import RawStore
from .tls import ca_bundle

# The two files the offline build reads. Everything else under data/raw/ is
# the mirror these are derived from.
PROJECTS = os.path.join(config.RAW, "acquisition_projects.json")
TARGET_DISTRICTS = os.path.join(config.RAW, "target_districts.json")


def refresh(since=config.EARLIEST_YEAR, until=None, dry_run=False, limit=None,
            summarise_only=False, reparse=False, log=print):
    """Harvest, parse and publish. Returns a report dict.

    `summarise_only` republishes the contract from whatever the mirror
    already holds, without opening a socket. A full harvest runs for hours
    and will be interrupted; without this, an interrupted run would leave the
    mirror full and the build with nothing to read.
    """
    until = until or date.today().year
    store = RawStore(config.RAW)
    if reparse:
        return _summarise(store, log, harvested=0, skipped=0, failed=0,
                          reparsed=_reparse(store, log))
    if summarise_only:
        return _summarise(store, log, harvested=0, skipped=0, failed=0)

    verify = ca_bundle(config.EGAZETTE_HOST, config.CACHE)
    if verify is None:
        log("could not repair the e-Gazette certificate chain; skipping the gazette harvest")
        return _summarise(store, log, harvested=0, skipped=0, failed=0)

    fetcher = Fetcher(httpx_transport(verify=verify),
                      min_interval=config.MIN_REQUEST_INTERVAL, retries=config.MAX_RETRIES)
    counts = _harvest(fetcher, store, since, until, dry_run, limit, log)
    _refresh_gazetteer(fetcher, log)
    return _summarise(store, log, **counts)


def _harvest(fetcher, store, since, until, dry_run, limit, log):
    harvested = skipped = failed = 0
    search = MinistrySearch(fetcher)

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
                     if is_land_acquisition(e.subject) and not store.has_seen(e.doc_id)),
                    key=lambda e: fetch_priority(e.subject))
                if entries:
                    log(f"  {ministry} {year}-{month:02d}: {len(entries)} listed, "
                        f"{len(wanted)} new to fetch")
                if dry_run:
                    continue

                for entry in wanted:
                    if limit is not None and harvested >= limit:
                        log(f"  reached the limit of {limit} documents")
                        return {"harvested": harvested, "skipped": skipped, "failed": failed}
                    outcome = _ingest_one(fetcher, store, entry)
                    harvested += outcome == "kept"
                    skipped += outcome == "skipped"
                    failed += outcome == "failed"
    return {"harvested": harvested, "skipped": skipped, "failed": failed}


def _ingest_one(fetcher, store, entry):
    """Download, parse and file one notification.

    Every outcome is recorded, including rejection, so the next run does not
    re-decide it and a small yield stays explainable from `index.json`.
    """
    try:
        pdf = documents.fetch(fetcher, entry.doc_id, entry.year)
    except PermanentError as exc:
        store.record(entry.doc_id, kept=False, reason=f"unavailable: {exc}")
        return "skipped"
    except TransientError:
        # Not recorded: a transient failure must stay eligible for the next
        # run, or one bad afternoon permanently loses a project.
        return "failed"

    text = documents.to_text(pdf)
    if text is None:
        return "failed"

    notification = parse(text, doc_id=entry.doc_id)
    if notification is None or notification.section not in ("3A", "3D"):
        # The catalog's free-text subject said land acquisition; the
        # document's own operative clause disagrees, and it wins.
        reason = "not an acquisition notification"
        if notification is not None:
            reason = f"section {notification.section}, no acquisition clock"
        store.record(entry.doc_id, kept=False, reason=reason)
        return "skipped"

    store.put_document(entry.doc_id, entry.year, pdf)
    store.put_notification(entry.doc_id, asdict(notification))
    store.record(entry.doc_id, kept=True)
    return "kept"


def _reparse(store, log):
    """Re-read every mirrored PDF through the current parser.

    Needed whenever `parse.py` improves, which so far has been every time a
    new batch of real documents arrived. Re-downloading twenty thousand PDFs
    to correct a regex is not an option, and they are already on disk.
    """
    count = 0
    for record in store.read_notifications():
        doc_id = record.get("doc_id")
        pdf = store.read_document(doc_id) if doc_id else None
        if pdf is None:
            continue
        text = documents.to_text(pdf)
        if text is None:
            continue
        notification = parse(text, doc_id=doc_id)
        if notification is None:
            continue
        store.put_notification(doc_id, asdict(notification))
        count += 1
    log(f"  reparsed {count} mirrored documents")
    return count


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


def _summarise(store, log, harvested, skipped, failed, reparsed=0):
    """Write the two files the pipeline reads.

    The per-document JSON under `notifications/` is the mirror; these two are
    the *contract*, in the same way `data/input/*.parquet` is the contract
    between s0 and s1. Publishing them means the pipeline needs one well-known
    path and no knowledge of how the mirror is laid out - the dependency
    stays a file, not an import.
    """
    notifications = store.read_notifications()
    projects = assemble(_rehydrate(n) for n in notifications)
    _write_json(PROJECTS, [_projectable(p) for p in projects])
    chosen = select(notifications, config.DISTRICTS_PER_STATE, config.ALWAYS_INCLUDE_DISTRICTS)
    _write_json(TARGET_DISTRICTS, chosen)

    report = {
        "harvested": harvested, "skipped": skipped, "failed": failed,
        "reparsed": reparsed,
        "notifications_on_disk": len(notifications),
        "declarations": sum(1 for n in notifications if n.get("section") == "3D"),
        "intentions": sum(1 for n in notifications if n.get("section") == "3A"),
        "projects": len(projects),
        "closed_projects": sum(1 for p in projects if p.declared_3d_on),
        "target_districts": chosen,
    }
    log(f"[ingest] {report['notifications_on_disk']} notifications on disk "
        f"({report['declarations']} x 3D, {report['intentions']} x 3A) "
        f"-> {report['projects']} projects, {report['closed_projects']} with a real "
        f"3A->3D interval; +{harvested} kept, {skipped} skipped, {failed} failed this run")
    for state, names in chosen.items():
        log(f"  {state}: {', '.join(names)}")
    return report


def _rehydrate(record):
    """A stored notification back into a `Notification`.

    Dates round-trip through the JSON as ISO strings; everything downstream
    of `assemble` compares and subtracts them, so they have to come back as
    dates rather than as strings that happen to sort correctly.
    """
    fields = dict(record)
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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)


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
                        help="re-read every mirrored PDF through the current parser, no network")
    args = parser.parse_args(argv)
    refresh(since=args.since, until=args.until, dry_run=args.dry_run, limit=args.limit,
            summarise_only=args.summarise_only, reparse=args.reparse)
    return 0


if __name__ == "__main__":
    sys.exit(main())
