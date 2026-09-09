# e-Gazette parser fixtures

Real MoRTH notifications published in the Gazette of India, Part II Section 3 Sub-section
(ii), harvested 2026-09-10. The filename is the gazette document id — `265102.txt` is
`CG-DL-E-30072025-265102`, retrievable at
`https://egazette.gov.in/WriteReadData/2025/265102.pdf`.

Text was extracted with `pdftotext -layout`, which is what the parser sees.

## These are redacted, and the redaction is the point

Every §3A and §3D notification annexes a schedule listing the land being acquired. In many
states that schedule includes a **`Name of Land Owner/Interested Person`** column naming
private individuals. That is personal data about real people, and it is not going into a
public repository.

So the fixtures are built by **whitelist, not blacklist**: only lines the parser actually
reads survive — the gazette id, the part/section header, the operative `S.O. …(E)`
paragraph, `SCHEDULE`, the `State:` / `District:` / `Taluk:` / `Village:` structure lines,
and the `[F. No …]` trailer. Everything else is dropped.

A blacklist was tried first and rejected: schedule rows wrap across lines, so name
fragments survived as bare tokens (`MATHEW`, `KOCHUKUNJU`) that no row-shaped pattern
matches. A whitelist cannot leak what it does not name.

The consequence for tests is deliberate. The parser is exercised on real government text —
real wording, real Devanagari interleaving, real page furniture, real inconsistency — but
never on a real person's name.

## What each fixture is for

| Fixture | Section | Covers |
|---|---|---|
| `265102` | 3D | The canonical case: cites its parent 3A's number and date, so one document yields a complete labelled interval |
| `265157`, `265154` | 3D | Layout variants |
| `265109`, `265108`, `265103`, `265101`, `265090`, `265089`, `265088`, `265087`, `265085` | 3A | Multi-district and multi-village schedules; Devanagari `[फा. सं. …]` file-number trailer |
| `265086`, `265084` | 3 (clause (a)) | Competent-authority appointments. The catalog's `Subject` column calls these "Section 3a", which is why the subject line is a filter hint and never the classification |

`265086` is the reason the parser classifies from the document body rather than from the
search-result metadata: its subject says `3a`, its body says `clause (a) of section 3`, and
it is not a land-acquisition notification at all.
