# Dynamic acquisition ingestion

Mirrors real land-acquisition notifications from the Gazette of India into `data/raw/`, so
the risk engine can train on statutory dates that actually happened instead of on dates a
generator invented.

```bash
make ingest                          # everything from 2018; long-running, resumable
make ingest ARGS="--since 2025"      # recent years only
make ingest ARGS="--limit 200"       # bounded run
make ingest ARGS="--dry-run"         # discover, download nothing
```

**Nothing here runs during `make build`.** The build reads `data/raw/` and never opens a
socket — same rule as the rest of the project.

---

## Why this exists

`data/raw/bhoomirashi/` held one `.gitkeep`. Every acquisition row was generated from
`RISK_SEED`, so every `is_delayed` label was synthetic, so `n_test_real = 0` for all five
stages and no metric could honestly be reported as a real-world claim. The implementation
blueprint calls this risk **X-1**, "the single largest risk".

This closes it for the stage that matters most: **§3A → §3D**, the 365-day clock whose breach
voids the notification under s.3D(3).

## What it produces

| File | Contents |
|---|---|
| `data/raw/acquisition_projects.json` | Projects with real statutory dates — **the contract** |
| `data/raw/target_districts.json` | The eight busiest districts per state |
| `data/raw/egazette/notifications/*.json` | The parsed mirror, one file per document (committed) |
| `data/raw/egazette/pdf/**` | Source PDFs (gitignored; re-fetchable by document id) |
| `data/raw/egazette/index.json` | Every document already considered, and why it was kept or skipped |

Consuming it is specified in
[`docs/specs/2026-09-10-acquisition-contract-handoff.md`](../docs/specs/2026-09-10-acquisition-contract-handoff.md).

## Layout

Each module has one reason to change; that is the test used when splitting them.

| Module | Responsibility |
|---|---|
| `config.py` | *What* to ingest. Pure data, no behaviour |
| `http.py` | Get bytes politely and reliably: rate limit, retry, backoff |
| `tls.py` | Repair the source's incomplete certificate chain |
| `store.py` | The on-disk mirror, and what has already been considered |
| `egazette/catalog.py` | Read the search-result grid. **Pure** |
| `egazette/search.py` | Drive the ASP.NET search form |
| `egazette/documents.py` | Retrieve a PDF and extract its text |
| `egazette/parse.py` | Document text → a `Notification`. **Pure** |
| `projects.py` | Notifications → projects with dated stages. **Pure** |
| `districts.py` | Rank and pick target districts. **Pure** |
| `bhoomirashi/gazetteer.py` | Official state → district → tehsil master data |
| `refresh.py` | Sequencing and reporting. Thin |

Five of those are pure functions over plain data. They hold all the logic capable of being
wrong, and all of it is tested offline against committed fixtures.

## Four things worth knowing

**A §3D is self-describing.** It states its own date *and* recites the date of the §3A it
closes, so one document yields a complete labelled interval. Harvesting the matching §3A is
an enrichment, not a prerequisite — which is what stops yield collapsing when discovery
misses a document.

**The catalog's `Subject` column is a hint, never the classification.** It is free text typed
by hand: the same month carries "Publication of notification under Section 3D" and a bare "3D
Notification", and rows reading "Section 3a" are usually competent-authority appointments
with no clock at all. The filter is tuned for recall — a false positive costs one fetch, a
false negative loses a project permanently — and the document's own operative clause decides
what it actually is.

**`egazette.gov.in` serves a broken certificate chain.** The leaf is signed by Let's Encrypt
intermediate `YR2`, which the server never sends, so every stock client fails with `unable to
get local issuer certificate`. `YR2` is signed by `ISRG Root YR`, which is in no trust store
yet, and *that* is cross-signed by `ISRG Root X1`, which is. `tls.py` follows the chain from
each certificate's Authority Information Access URL until it reaches something already
trusted. Verification is never weakened — the chain still terminates at a trusted root; the
only change is supplying links the server should have sent itself.

**Discovery is fragile; retrieval is not.** The search is ASP.NET WebForms with a cookieless
session, ViewState tokens and AutoPostBack dropdowns. It works today and it will break when
the site is next rebuilt. Retrieving a PDF needs no session at all. So a broken search
degrades to "no new notifications found" and the build carries on against whatever is already
mirrored — never to a broken build.

## Fixtures name no one

Every §3A and §3D annexes a schedule of the land being taken, and in several states that
schedule includes a `Name of Land Owner/Interested Person` column naming private individuals.
Fixtures are built by whitelist, and any document whose schedule names people keeps only its
structural lines. See `tests/fixtures/egazette/README.md`.

## Politeness

These are public services running on public money. The defaults are 1.5 s between requests,
three retries with exponential backoff, no retry on a 404, and an identifying User-Agent.
Everything is cached, so a re-run costs the source nothing.

The Bhoomi Rashi *project search* is CAPTCHA-gated and is deliberately not automated. Only
its open dropdown endpoints are used, for district and tehsil master data. The labelled spine
comes from e-Gazette, which has no CAPTCHA.
