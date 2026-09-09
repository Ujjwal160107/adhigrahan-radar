"""Bhoomi Rashi's own state -> district -> tehsil master data.

The portal's cascading dropdowns are served by two endpoints that take a
plain GET, carry no CAPTCHA and need no session. They return the official
names and ids the Ministry itself uses, which makes them the right authority
for reconciling a district name read out of a gazette PDF against a district
the portal recognises.

The project search on the same portal *is* CAPTCHA-gated. It is deliberately
not automated - see the design doc. The labelled spine comes from e-Gazette,
which has no CAPTCHA, and this module supplies the vocabulary to align it.

The response format is the portal's own: `id|$|Name|~|id|$|Name`.
"""
from .. import config

_PAIR = "|$|"
_SEPARATOR = "|~|"


def parse_options(body):
    """`"185|$|SULTANPUR |~|640|$|Amethi"` -> `{"185": "SULTANPUR", ...}`.

    Pure, and separated from fetching so the format - which is neither JSON
    nor HTML and has no specification - is pinned by tests.
    """
    options = {}
    for chunk in (body or "").split(_SEPARATOR):
        key, sep, name = chunk.partition(_PAIR)
        if not sep:
            continue
        key, name = key.strip(), name.strip()
        # "-1" is the "-- DISTRICT --" placeholder, not a place.
        if key and name and key != "-1":
            options[key] = name
    return options


def districts(fetcher, state_id):
    """Every district the portal knows in one state, by portal id."""
    url = config.BHOOMIRASHI_DISTRICTS.format(state_id=state_id)
    return parse_options(fetcher.get(url).text)


def tehsils(fetcher, district_id):
    """Every tehsil in one district, by portal id."""
    url = config.BHOOMIRASHI_PLACES.format(district_id=district_id)
    return parse_options(fetcher.get(url).text)
