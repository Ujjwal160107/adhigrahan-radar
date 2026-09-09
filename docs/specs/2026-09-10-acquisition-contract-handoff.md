# Real acquisition data — handoff to the ML side

**Date:** 2026-09-10 · **Produces:** `data/raw/acquisition_projects.json`,
`data/raw/target_districts.json` · **Consumes:** nothing in `pipeline/`

The ingest layer stops here. It writes two files and changes nothing in `pipeline/`,
`backend/` or `frontend/`. This document is what you need to wire them into the risk engine.

---

## 1. Get the data

```bash
make ingest                       # everything from 2018; long-running, resumable
make ingest ARGS="--since 2025"   # recent years only
make ingest ARGS="--limit 200"    # bounded run, safe to stop and restart
```

It is incremental. Every document already considered is recorded in
`data/raw/egazette/index.json`, including the ones that were rejected and why, so a second
run fetches only what is new and an interrupted run resumes rather than restarting.

A full harvest is roughly twenty thousand documents at a couple of megabytes each — one
MoRTH month alone carries 59 §3A and 53 §3D notifications. Expect hours, and run it in the
background. The work is ordered so that **any prefix of it is the most useful harvest of
that size**: newest years first, and §3D before §3A, because a §3D yields a complete
labelled interval on its own.

`make build` never touches the network. It reads `data/raw/`.

---

## 2. `acquisition_projects.json`

A list of projects, each one stretch of highway with its statutory dates.

```json
{
  "project_id": "PRJ-SUL-G265102",
  "state": "UTTAR PRADESH",
  "districts": ["SULTANPUR"],
  "villages": ["Kudwar", "Madanpur Panyar"],
  "nh_no": "NH66",
  "km_from": 417.0,
  "km_to": 460.7,
  "notified_3a_on": "2024-12-23",
  "declared_3d_on": "2025-07-29",
  "republication_count": 0,
  "so_number": "S.O. 3514(E)",
  "area_hectares": 12.5,
  "source_doc_ids": [265102]
}
```

| Field | Meaning |
|---|---|
| `project_id` | `PRJ-<district>-G<gazette doc id>`. Traceable: `265102` retrieves at `https://egazette.gov.in/WriteReadData/2025/265102.pdf` |
| `notified_3a_on` | The §3A date — **the clock starts here** |
| `declared_3d_on` | The §3D date, or `null` if the clock is still running |
| `republication_count` | How many times the §3A was re-published over this stretch |
| `area_hectares` | Summed from the notification's own schedule. Never derived from chainage |
| `source_doc_ids` | Every gazette document this project was built from |

### The label

`notified_3a_on` → `declared_3d_on` is the **s.3D(3) 365-day statutory clock**. Missing it
lapses the notification. That maps exactly onto the existing `notification_3a_11` stage,
whose `clock_authority` is already `NH Act 1956 s.3D(3)`:

```
started_on   = notified_3a_on
completed_on = declared_3d_on      # null => open, right-censored
statutory_days = 365
```

`declared_3d_on is null` is an **open stage**, not an on-time one. `s9` already refuses to
coerce an unfinished stage's `is_delayed` to 0; the same discipline applies here.

Three real intervals from the first harvest: **218, 357 and 363 days**. Two came within days
of lapsing. That near-miss density is the signal a delay model needs and the synthetic
generator cannot produce honestly.

---

## 3. What is real, and what is missing

Real, stated by the gazette:

`notified_3a_on` · `declared_3d_on` · `nh_no` · `km_from` · `km_to` · `state` · `districts` ·
`villages` · `area_hectares` · `republication_count` · `so_number`

**`republication_count` is worth a second look.** It is currently invented by
`s8`'s generator. Here it is a real count of how many times an authority had to re-publish a
§3A — which is what an authority does when the first one is about to lapse. It is plausibly
the strongest real feature in the set.

Not stated by the gazette, and therefore absent:

| Field | Why |
|---|---|
| `affected_families` | Never published. The schedule lists plots, and in some states owners, but not households |
| `budget_estimate_inr` | Never published |
| `executing_agency`, `block` | Not in the notification text |

**These are absent, not zero.** `s12` already imputes numeric features on the *training
split* median and reuses those medians on the holdout, so a null is handled leakage-safely
and disclosed. Do not fill them in here — an imputed value that reaches the contract is
indistinguishable from a measurement, and that is exactly what honesty rule 1 exists to
prevent.

### Only stage 1 gets a real label

Awards under §3G, compensation disbursement and taking of possession are **never gazetted**.
`declaration_3d_19`, `award_3g_23`, `compensation_disbursed` and `possession` therefore stay
synthetic. This is a limit of the public record, not of the pipeline.

The consequence for the model registry: `n_test_real` must be reported **per stage**, not
globally. Stage 1 can carry a real number for the first time. Stages 2–5 report exactly what
they report today.

---

## 4. `target_districts.json`

```json
{"UTTAR PRADESH": ["SULTANPUR", "GONDA", ...], "BIHAR": ["PATNA", ...]}
```

The eight districts per state with the most notifications, ranked from the harvest, with
Sultanpur force-included — it is the only district with a real litigation corpus, and the
flagship project binds to a parcel in it.

Districts are chosen by the data rather than declared up front because the gazette does not
distribute acquisitions evenly: one corridor project can dominate a state's whole year, and
eight names picked in advance can easily be eight districts with nothing in them.

The harvest keeps **every** state it parses, not just these two. Widening scope later is a
filtering change, not a re-harvest.

---

## 5. Three things that will bite

**Names are uppercase.** The gazette prints `SULTANPUR`; the existing corpus uses
`Sultanpur`. Left alone they are two districts for one place and every district-level
feature splits in half. Normalise on the way in.

**`nh_no` can be null.** Greenfield alignments have no highway number, so it can never be a
join key. Use `(state, nh_no, km_from, km_to)` or the `project_id`.

**One project, one stage row.** A real project emits only `notification_3a_11`. Anything that
assumes five stages per project needs to handle that — `s9` itself does not, but check
whatever you add.

---

## 6. If you need to re-parse

Everything is re-derivable without touching the network. The source PDFs are under
`data/raw/egazette/pdf/` (gitignored — re-fetchable by document id), and the parser is pure:

```python
from ingest.egazette.parse import parse
from ingest.egazette import documents
from ingest.projects import assemble

notification = parse(documents.to_text(pdf_bytes), doc_id=265102)
projects = assemble(notifications)
```

`parse` and `assemble` take text and records and return records — no IO, no network — so
they can be exercised directly against `tests/fixtures/egazette/`. If you need a field the
parser does not extract yet, add it there and the fixtures will tell you immediately whether
it works across the real layout variance.
