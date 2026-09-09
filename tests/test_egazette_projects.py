"""Assembling parsed notifications into projects with dated stages.

The rule that shapes this module: a §3D is self-describing. It states its own
date and recites the date of the §3A it closes, so one document yields a
complete, closed, real interval. A §3A that no §3D claims is an *open*
project - the right-censored row the risk model actually scores.

What this module deliberately does NOT do is decide whether a stage was
delayed. `s9_acquisition_ingest` already derives `deadline_on`,
`overdue_days` and `is_delayed` from dates and a statutory clock. Deriving
them here as well would be the same rule in two places, free to drift.
"""
import os
from datetime import date

from ingest.egazette.parse import parse
from ingest.egazette.records import Notification
from ingest.projects import assemble

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "egazette")


def notification(doc_id, section, notified_on, parent=None, state="UTTAR PRADESH",
                 nh_no="NH31", km=(0.0, 10.0), districts=("SULTANPUR",), villages=("Kudwar",)):
    return Notification(
        doc_id=doc_id, section=section, notified_on=notified_on,
        parent_notified_on=parent, state=state, nh_no=nh_no,
        km_from=km[0], km_to=km[1], districts=districts, villages=villages)


class TestClosedProjects:
    def test_a_declaration_yields_one_project_with_both_dates(self):
        d3d = notification(900, "3D", date(2025, 7, 29), parent=date(2024, 12, 23))

        (project,) = assemble([d3d])

        assert project.notified_3a_on == date(2024, 12, 23)
        assert project.declared_3d_on == date(2025, 7, 29)

    def test_a_declaration_alone_is_enough(self):
        # The matching §3A need not have been harvested. A §3D recites its
        # parent's date, so the interval is complete without it - which is
        # what keeps yield from collapsing when discovery misses a document.
        (project,) = assemble([notification(900, "3D", date(2025, 7, 29),
                                            parent=date(2024, 12, 23))])
        assert project.notified_3a_on is not None

    def test_a_declaration_with_no_recited_parent_yields_no_project(self):
        # Without the §3A date there is no interval and no label, and a
        # project with one endpoint would be silently mis-scored.
        assert assemble([notification(900, "3D", date(2025, 7, 29), parent=None)]) == []


class TestOpenProjects:
    def test_an_unclaimed_intention_yields_an_open_project(self):
        (project,) = assemble([notification(900, "3A", date(2025, 1, 10))])

        assert project.notified_3a_on == date(2025, 1, 10)
        assert project.declared_3d_on is None

    def test_an_intention_closed_by_a_declaration_is_not_counted_twice(self):
        a3a = notification(900, "3A", date(2024, 12, 23))
        d3d = notification(901, "3D", date(2025, 7, 29), parent=date(2024, 12, 23))

        projects = assemble([a3a, d3d])

        assert len(projects) == 1
        assert projects[0].declared_3d_on == date(2025, 7, 29)

    def test_a_declaration_does_not_claim_an_intention_in_another_state(self):
        # Two notifications can share a date by coincidence; state and
        # chainage are what make the match real.
        a3a = notification(900, "3A", date(2024, 12, 23), state="BIHAR")
        d3d = notification(901, "3D", date(2025, 7, 29), parent=date(2024, 12, 23),
                           state="KERALA")

        assert len(assemble([a3a, d3d])) == 2

    def test_a_declaration_does_not_claim_a_disjoint_stretch(self):
        a3a = notification(900, "3A", date(2024, 12, 23), km=(0.0, 10.0))
        d3d = notification(901, "3D", date(2025, 7, 29), parent=date(2024, 12, 23),
                           km=(80.0, 95.0))

        assert len(assemble([a3a, d3d])) == 2


class TestMissingChainage:
    """Not every notification states a chainage - some Greenfield alignments
    and some older templates simply do not. Grouping sorts by stretch, and a
    None cannot be compared to a float, so a single such notification used to
    take down the whole assembly with a TypeError."""

    def test_a_notification_without_chainage_still_yields_a_project(self):
        (project,) = assemble([notification(900, "3A", date(2025, 1, 10), km=(None, None))])
        assert project.km_from is None

    def test_it_groups_alongside_notifications_that_do_have_chainage(self):
        projects = assemble([
            notification(900, "3A", date(2025, 1, 10), km=(None, None)),
            notification(901, "3A", date(2025, 2, 10), km=(0.0, 10.0)),
            notification(902, "3A", date(2025, 3, 10), km=(None, None), nh_no="NH99"),
        ])
        assert len(projects) == 3

    def test_ordering_stays_deterministic_with_missing_chainage(self):
        rows = [
            notification(900, "3A", date(2025, 1, 10), km=(None, None)),
            notification(901, "3A", date(2025, 2, 10), km=(0.0, 10.0)),
        ]
        assert ([p.project_id for p in assemble(rows)]
                == [p.project_id for p in assemble(list(reversed(rows)))])


class TestRepublication:
    def test_counts_repeated_intentions_over_the_same_stretch(self):
        # Re-publishing a §3A is what an authority does when the first one is
        # about to lapse. It is a real, causal delay signal, and until now
        # `gazette_republication_count` was invented by the synthetic generator.
        notifications = [
            notification(900, "3A", date(2023, 5, 1)),
            notification(901, "3A", date(2024, 6, 1)),
            notification(902, "3A", date(2025, 7, 1)),
        ]

        projects = assemble(notifications)

        assert len(projects) == 1
        assert projects[0].republication_count == 2

    def test_a_single_notification_is_not_a_republication(self):
        (project,) = assemble([notification(900, "3A", date(2025, 1, 10))])
        assert project.republication_count == 0

    def test_the_latest_intention_starts_the_clock(self):
        # A re-published §3A restarts the s.3D(3) clock; scoring against the
        # superseded one would report a breach that has not happened.
        projects = assemble([
            notification(900, "3A", date(2023, 5, 1)),
            notification(901, "3A", date(2025, 7, 1)),
        ])
        assert projects[0].notified_3a_on == date(2025, 7, 1)


class TestIrrelevantDocuments:
    def test_competent_authority_appointments_are_dropped(self):
        # A §3 appointment has no clock and no schedule of land.
        assert assemble([notification(900, "3", date(2025, 7, 29))]) == []

    def test_an_undated_notification_is_dropped(self):
        assert assemble([notification(900, "3A", None)]) == []


class TestCarriedFields:
    """Fields the pipeline needs that live on the notification, not on the
    grouping. They are carried onto the project so that `data/raw`'s
    published contract is projects alone - the pipeline then never has to
    re-open the notification mirror or know how it is laid out."""

    def test_the_order_number_travels_with_the_project(self):
        (project,) = assemble([Notification(
            doc_id=265102, section="3D", notified_on=date(2025, 7, 29),
            parent_notified_on=date(2024, 12, 23), so_number="S.O. 3514(E)")])
        assert project.so_number == "S.O. 3514(E)"

    def test_the_measured_area_travels_with_the_project(self):
        (project,) = assemble([Notification(
            doc_id=265102, section="3D", notified_on=date(2025, 7, 29),
            parent_notified_on=date(2024, 12, 23), area_hectares=12.5)])
        assert project.area_hectares == 12.5

    def test_an_open_project_carries_the_latest_notifications_area(self):
        # A re-published §3A supersedes its predecessor, including its
        # schedule: the land being taken may have changed.
        projects = assemble([
            notification(900, "3A", date(2023, 5, 1)),
            Notification(doc_id=901, section="3A", notified_on=date(2025, 7, 1),
                         state="UTTAR PRADESH", nh_no="NH31", km_from=0.0, km_to=10.0,
                         districts=("SULTANPUR",), area_hectares=99.0),
        ])
        assert projects[0].area_hectares == 99.0


class TestIdentity:
    def test_project_ids_are_unique(self):
        projects = assemble([
            notification(900, "3A", date(2025, 1, 1), km=(0.0, 10.0)),
            notification(901, "3A", date(2025, 1, 1), km=(50.0, 60.0)),
        ])
        assert len({p.project_id for p in projects}) == len(projects)

    def test_project_id_is_traceable_to_its_source_document(self):
        (project,) = assemble([notification(265102, "3D", date(2025, 7, 29),
                                            parent=date(2024, 12, 23))])
        assert "265102" in project.project_id

    def test_assembly_is_deterministic(self):
        notifications = [
            notification(902, "3A", date(2025, 3, 1), km=(20.0, 30.0)),
            notification(900, "3D", date(2025, 7, 29), parent=date(2024, 12, 23)),
            notification(901, "3A", date(2025, 1, 1), km=(50.0, 60.0)),
        ]
        assert ([p.project_id for p in assemble(notifications)]
                == [p.project_id for p in assemble(list(reversed(notifications)))])


class TestAgainstRealDocuments:
    def test_the_real_declarations_yield_dated_intervals(self):
        notifications = []
        for doc_id in (265102, 265154, 265157):
            with open(os.path.join(FIXTURES, f"{doc_id}.txt"), encoding="utf-8") as fh:
                notifications.append(parse(fh.read(), doc_id=doc_id))

        projects = assemble(notifications)

        assert len(projects) == 3
        for p in projects:
            assert p.notified_3a_on < p.declared_3d_on
            # All three real intervals are under the s.3D(3) year - two of
            # them by a handful of days (357 and 363), which is exactly the
            # near-miss signal a delay model needs to see.
            assert (p.declared_3d_on - p.notified_3a_on).days < 365
