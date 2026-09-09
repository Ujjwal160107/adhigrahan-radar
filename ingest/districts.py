"""Choosing which districts the corpus covers.

Pure. Districts are picked from the harvest rather than declared in advance,
because the gazette does not distribute acquisitions evenly - a single
corridor project can account for most of a state's notifications in a year,
and eight district names chosen up front can easily be eight districts with
nothing in them.
"""
from collections import Counter


def select(notifications, per_state, always_include=()):
    """The busiest `per_state` districts in each state.

    `always_include` names districts to keep regardless of rank, but only if
    they actually appear in the harvest: forcing in a district with no
    notifications would put an empty district in the corpus, where every
    feature is null and every model reads the absence as a signal.

    Ties break alphabetically so that two runs over the same harvest choose
    the same districts.
    """
    forced = {d.upper() for d in always_include}
    counts = _count(notifications)

    chosen = {}
    for state, per_district in counts.items():
        ranked = sorted(per_district, key=lambda d: (-per_district[d], d))
        keep = [d for d in ranked if d.upper() in forced]
        keep += [d for d in ranked if d not in keep][:max(0, per_state - len(keep))]
        if keep:
            chosen[state] = sorted(keep, key=lambda d: (-per_district[d], d))
    return chosen


def _count(notifications):
    """Notifications per (state, district).

    A notification spanning several districts counts once for each: the
    acquisition really is happening in all of them.
    """
    counts = {}
    for row in notifications:
        state = (row.get("state") or "").strip()
        districts = [d.strip() for d in (row.get("districts") or []) if d and d.strip()]
        if not state or not districts:
            continue
        counts.setdefault(state, Counter()).update(districts)
    return counts
