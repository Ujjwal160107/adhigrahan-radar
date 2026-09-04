# Documentation index

| Folder | Holds | Read when |
|---|---|---|
| `architecture/` | System architecture, the frontend design system, and both excalidraw diagrams with the script that generates them | You need to know how the pieces fit |
| `specs/` | Approved designs. Newest first. | You are about to build something |
| `plans/` | Implementation plans that were executed | You want to know how a thing came to be |
| `product/` | The PRD and SIH submission content | You are writing a slide or a submission |
| `research/` | Source discovery, verified data sources, dead ends | You are about to scrape or trust a source |

## Start here

1. **[`../README.md`](../README.md)** - what the project is and how to run it
2. **[`architecture/adhigrahan-radar-architecture.md`](architecture/adhigrahan-radar-architecture.md)** - the layered view and the data model
3. **[`../pipeline/README.md`](../pipeline/README.md)** - every stage, and every deliberate deviation
4. **[`specs/2026-08-30-sih26017-acquisition-delay-design.md`](specs/2026-08-30-sih26017-acquisition-delay-design.md)** - the risk engine, in full

## Diagrams

Both `.excalidraw` files are generated, not hand-edited. Open them at
[excalidraw.com](https://excalidraw.com) (File -> Open), and regenerate with:

```bash
make diagrams
```

Edit `architecture/make_diagrams.py`, never the JSON - hand edits are lost on the next
regeneration.

## A note on § references

Numbered section references throughout the codebase and the specs (`PRD §19`, `§37`, `§46`)
point at [`product/vivaad-radar-prd.md`](product/vivaad-radar-prd.md). That document
specifies the **linkage subsystem**. Where the acquisition-delay design supersedes it, the
newer spec says so explicitly rather than leaving the two in silent conflict.
