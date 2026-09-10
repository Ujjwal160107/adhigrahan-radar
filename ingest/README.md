# Dynamic acquisition ingestion

Mirrors real land-acquisition notifications from the Gazette of India into `data/raw/`, so
the risk engine can train on statutory dates that actually happened instead of on dates a
generator invented.

```bash
make ingest                          # everything from 2018; long-running, resumable
make ingest ARGS="--since 2025"      # recent years only
make ingest ARGS="--limit 200"       # bounded run
make ingest ARGS="--dry-run"         # discover, download nothing
make ingest ARGS="--summarise-only"  # republish the contract from the mirror, no network
make ingest ARGS="--reparse"         # re-read the mirror through the current parser, no network
make ingest ARGS="--verify"          # rehash the mirror against the journal, no network
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
| `data/raw/acquisition_projects.manifest.json` | Fingerprint of the contract and the corpus it came from |
| `data/raw/egazette/notifications/*.json` | The parsed mirror, one file per document (committed) |
| `data/raw/egazette/journal.ndjson` | **Append-only record of every decision ever made** — the source of truth |
| `data/raw/egazette/index.json` | Every document already considered, and why. Derived from the journal; rebuildable |
| `data/raw/egazette/index.meta.json` | What that cache was projected from, and the corpus hash |
| `data/raw/egazette/rejected/*.txt.gz` | The text a rejection was decided from (committed) |
| `data/raw/egazette/text/*.txt.gz` | Text for the kept corpus (gitignored; a local reparse convenience) |
| `data/raw/egazette/pdf/**` | Source PDFs (gitignored; re-fetchable by document id) |
| `data/raw/egazette/runs/*/manifest.json` | What each run attempted, and what it achieved |

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

## Running it twice is safe

A second run over an unchanged mirror **writes nothing at all** — not the contract, not
the index, not the manifest. That is the property everything below exists to protect, and
it is checked by `tests/test_ingest_durability.py`.

**Every write is atomic.** Write to a temporary file in the same directory, fsync, then
`os.replace`. A reader sees the old file or the new one, never half of either. Before
this, an interruption mid-write could truncate `index.json`, and a truncated `index.json`
raised inside `RawStore.__init__` — so *every* entry point died, including
`--summarise-only`, which exists precisely to recover from an interrupted harvest.

**Decisions are appended, never rewritten.** `journal.ndjson` is the source of truth and
grows by one line per decision; `index.json` is a projection of it. A crash can only tear
the last line, which is discarded on read. The old index was re-serialised in full after
*every* document, which is quadratic over a twenty-thousand-document harvest.

**A decision names the parser that made it.** Outcomes come from a closed vocabulary —
`kept`, `rejected`, `unavailable`, `unreadable`, `deferred` — and carry the SHA-256 of the
bytes they were made from. `deferred` is never a decision, so a transient failure stays
eligible. `unreadable` is one, so an image-only PDF stops costing a multi-megabyte fetch
on every harvest for ever.

**Rejections keep their evidence.** The extracted text of a rejected document is committed
under `rejected/`, so bumping `PARSER_VERSION` lets `--reparse` revisit that decision with
no network and no PDF. Previously the bytes were discarded and the index said "decided",
so a parser fix could never reach anything it had already turned away.

**One harvest at a time.** `data/raw/.lock` names the run holding the store, and a second
run refuses to start rather than silently erasing the first one's record. It never expires
by age — clearing a stale lock is `--force-unlock`, a deliberate act.

**A project keeps one identity.** `project_id` anchors on the *earliest* document known
for a stretch of highway, so a republished §3A or the §3D that finally closes it does not
rename the project every downstream table refers to. The dates still come from the latest
document, which is the one that supersedes.

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
