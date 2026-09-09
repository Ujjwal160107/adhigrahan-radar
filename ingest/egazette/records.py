"""The data contract between discovery, retrieval and parsing.

Plain frozen dataclasses, no behaviour and no IO, so every other module in
this package can depend on them without depending on each other.
"""
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Notification:
    """One notification as published in the Gazette of India.

    Every field except `doc_id` and `section` is optional, because the
    corpus really is that irregular: a Greenfield alignment has no highway
    number, a competent-authority appointment names no villages, and a
    §3A has no parent to recite. Nothing here is inferred or defaulted - a
    field the document does not state stays None, and the pipeline decides
    what to do about it.
    """

    doc_id: int
    section: str  # "3" | "3A" | "3D"
    so_number: str | None = None
    notified_on: date | None = None
    # Set only on a §3D: the date of the §3A it closes. `notified_on` minus
    # this is the s.3D(3) 365-day statutory clock, and therefore the label.
    parent_notified_on: date | None = None
    nh_no: str | None = None
    km_from: float | None = None
    km_to: float | None = None
    state: str | None = None
    districts: tuple[str, ...] = field(default_factory=tuple)
    villages: tuple[str, ...] = field(default_factory=tuple)
    # Summed from the schedule, never derived from the chainage.
    area_hectares: float | None = None
