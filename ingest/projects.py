"""Parsed notifications -> projects with dated stages.

Pure: records in, records out, no IO. The second of the two modules in this
package that hold logic capable of being wrong, and therefore the second that
is exhaustively tested without a network.

The assembly rests on one property of the corpus: **a §3D is
self-describing**. It states its own date and recites the date of the §3A it
closes, so a single document yields a complete interval. Harvesting the
matching §3A is an enrichment, not a prerequisite - which is what stops yield
collapsing when discovery misses a document.

    §3D found                  -> closed project, both dates real
    §3A no §3D claims          -> open project, right-censored
    §3 (competent authority)   -> dropped; it has no clock

What this module does not do is decide whether a stage was *delayed*.
`s9_acquisition_ingest` already derives `deadline_on`, `overdue_days` and
`is_delayed` from a start date, an end date and a statutory clock. Repeating
that rule here would put one derivation in two places, free to drift apart.
"""
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Project:
    """One acquisition of one stretch of highway, with its statutory dates.

    `declared_3d_on is None` means the §3A clock is still running: an open,
    right-censored stage, and the row the risk model exists to score.
    """

    project_id: str
    state: str | None
    districts: tuple[str, ...]
    villages: tuple[str, ...]
    nh_no: str | None
    km_from: float | None
    km_to: float | None
    notified_3a_on: date
    declared_3d_on: date | None
    # How many times the §3A was re-published over this stretch. An authority
    # re-publishes when the first notification is close to lapsing, so this
    # is a real, causal delay signal rather than a generated one.
    republication_count: int
    # Carried from the anchoring notification so that the published contract
    # is projects alone: the pipeline never re-opens the notification mirror
    # and never needs to know how it is laid out.
    so_number: str | None = None
    area_hectares: float | None = None
    source_doc_ids: tuple[int, ...] = field(default_factory=tuple)


def assemble(notifications):
    """Group notifications into projects. Deterministic: the result depends
    on the notifications, never on the order they arrive in."""
    usable = [n for n in notifications
              if n is not None and n.section in ("3A", "3D") and n.notified_on is not None]
    declarations = [n for n in usable if n.section == "3D" and n.parent_notified_on is not None]
    intentions = [n for n in usable if n.section == "3A"]

    projects = [_closed(d) for d in _sorted(declarations)]
    projects += [_open(group) for group in _unclaimed(intentions, declarations)]
    return sorted(projects, key=lambda p: p.project_id)


def _closed(declaration):
    return _project(
        anchor=declaration,
        notified_3a_on=declaration.parent_notified_on,
        declared_3d_on=declaration.notified_on,
        republication_count=0,
        doc_ids=(declaration.doc_id,),
    )


def _open(group):
    """One project from every §3A over the same stretch.

    The *latest* notification starts the clock: a re-published §3A supersedes
    its predecessor, and measuring from the superseded one would report a
    breach that has not happened.
    """
    latest = group[-1]
    return _project(
        anchor=latest,
        notified_3a_on=latest.notified_on,
        declared_3d_on=None,
        republication_count=len(group) - 1,
        doc_ids=tuple(n.doc_id for n in group),
    )


def _project(anchor, notified_3a_on, declared_3d_on, republication_count, doc_ids):
    return Project(
        project_id=_project_id(anchor),
        state=anchor.state,
        districts=anchor.districts,
        villages=anchor.villages,
        nh_no=anchor.nh_no,
        km_from=anchor.km_from,
        km_to=anchor.km_to,
        notified_3a_on=notified_3a_on,
        declared_3d_on=declared_3d_on,
        republication_count=republication_count,
        so_number=anchor.so_number,
        area_hectares=anchor.area_hectares,
        source_doc_ids=doc_ids,
    )


def _project_id(notification):
    """`PRJ-SUL-G265102` - district abbreviation plus the gazette document
    the row came from, so any figure in the product can be traced back to a
    retrievable government PDF."""
    district = notification.districts[0] if notification.districts else (notification.state or "XX")
    abbr = "".join(c for c in district.upper() if c.isalpha())[:3] or "XXX"
    return f"PRJ-{abbr}-G{notification.doc_id}"


def _unclaimed(intentions, declarations):
    """§3A notifications no §3D closed, grouped by the stretch they cover.

    A §3D claims a §3A when it recites that §3A's date *and* covers the same
    ground. The date alone is not enough - the Press publishes many
    notifications on one day, so two unrelated acquisitions routinely share a
    date."""
    groups = {}
    for n in _sorted(intentions):
        if any(_claims(d, n) for d in declarations):
            continue
        groups.setdefault(_stretch_key(n), []).append(n)
    return [groups[k] for k in sorted(groups)]


def _claims(declaration, intention):
    return (declaration.parent_notified_on == intention.notified_on
            and _same_state(declaration, intention)
            and _overlaps(declaration, intention))


def _same_state(a, b):
    return (a.state or "").strip().upper() == (b.state or "").strip().upper()


def _overlaps(a, b):
    """Chainage overlap, treating an unstated range as "cannot rule it out".

    A §3D sometimes covers only part of the stretch its §3A notified, so
    exact equality would leave real pairs unmatched.
    """
    if None in (a.km_from, a.km_to, b.km_from, b.km_to):
        return True
    return a.km_from <= b.km_to and b.km_from <= a.km_to


def _stretch_key(n):
    """Sortable, and total.

    Chainage is genuinely absent on some notifications - a few Greenfield
    alignments and some older templates never state it - and `None` cannot be
    compared to a float, so an unstated range sorts as its own group ahead of
    the stated ones rather than raising.
    """
    return ((n.state or "").upper(), n.nh_no or "",
            n.km_from is None, n.km_from or 0.0,
            n.km_to is None, n.km_to or 0.0)


def _sorted(notifications):
    """Chronological, with the document id breaking ties, so assembly never
    depends on the order discovery happened to return."""
    return sorted(notifications, key=lambda n: (n.notified_on, n.doc_id))
