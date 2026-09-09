"""Reading the e-Gazette Search-by-Ministry result grid.

This is the discovery step, and it is the cheap one: the grid states each
notification's subject, section, dates and document id, so the harvest can
decide what is worth downloading *before* spending a multi-megabyte PDF
fetch on it. One MoRTH month is ~45 rows, of which ~38 are land acquisition.

The fixture is the real grid for MoRTH, August 2025.
"""
import os
from datetime import date

from ingest.egazette.catalog import fetch_priority, is_land_acquisition, read_page

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "egazette")


def catalog_html():
    with open(os.path.join(FIXTURES, "catalog_morth_2025_08.html"), encoding="utf-8") as fh:
        return fh.read()


class TestReadingRows:
    def test_reads_every_data_row_and_no_header_row(self):
        entries = read_page(catalog_html())
        assert len(entries) == 15

    def test_reads_the_document_id_from_the_gazette_id(self):
        # "CG-DL-E-30082025-265818" -> 265818, which is the filename under
        # WriteReadData. The id is the only thing needed to retrieve the PDF.
        assert read_page(catalog_html())[1].doc_id == 265818

    def test_reads_the_publication_year_from_the_gazette_id(self):
        # WriteReadData is partitioned by year, so retrieval needs both.
        assert read_page(catalog_html())[1].year == 2025

    def test_reads_the_issue_date(self):
        assert read_page(catalog_html())[1].issue_date == date(2025, 8, 28)

    def test_reads_the_subject(self):
        assert "Section 3D" in read_page(catalog_html())[1].subject

    def test_an_empty_page_yields_no_entries(self):
        # A month with no notifications must be an empty harvest, not a crash.
        assert read_page("<html><body>no results</body></html>") == []


class TestPagination:
    def test_reports_the_pages_still_to_walk(self):
        # The August 2025 grid links pages 2 through 9; stopping at page one
        # would silently harvest a sixth of the month.
        assert read_page(catalog_html()).next_pages == (2, 3, 4, 5, 6, 7, 8, 9)

    def test_a_single_page_result_has_nothing_further_to_walk(self):
        assert read_page("<html><body>no results</body></html>").next_pages == ()


class TestLandAcquisitionFilter:
    """The filter that decides what gets downloaded. It is tuned for recall
    on purpose: a false positive costs one PDF fetch, a false negative loses
    a project from the corpus permanently."""

    def test_keeps_declarations(self):
        assert is_land_acquisition("Publication of notification under Section 3D")

    def test_keeps_intentions(self):
        assert is_land_acquisition("Publication of notification under Section 3A")

    def test_keeps_the_terse_variant(self):
        # The same August grid carries both "Publication of notification
        # under Section 3D" and a bare "3D Notification". The subject line is
        # typed by hand and is not a controlled vocabulary.
        assert is_land_acquisition("3D Notification")

    def test_keeps_lowercase_3a_which_may_still_be_an_acquisition(self):
        # Subjects reading "Section 3a" are usually competent-authority
        # appointments, but not always, and the catalog cannot tell them
        # apart. They are downloaded and then classified from the document
        # body, which can.
        assert is_land_acquisition("Publication of notification under Section 3a")

    def test_drops_user_fee_notifications(self):
        assert not is_land_acquisition("Publication of User Fee Notification")

    def test_drops_motor_vehicle_rule_amendments(self):
        assert not is_land_acquisition("Amendment the Central Motor Vehicles Rules 1989")

    def test_drops_an_empty_subject(self):
        assert not is_land_acquisition("")


class TestFetchOrder:
    """A full harvest is roughly 20,000 documents at a couple of megabytes
    each, over years - one MoRTH month alone carries 59 §3A and 53 §3D
    notifications. It will be interrupted, and it will be run with a budget.

    So the work is ordered by how much a document is worth, and any prefix of
    the harvest is the most useful harvest of that size. A §3D is worth most
    because it is self-describing: it recites its parent's date, so one
    document yields a complete labelled interval on its own. A §3A only adds
    an open, right-censored row."""

    def test_a_declaration_outranks_an_intention(self):
        assert (fetch_priority("Publication of notification under Section 3D")
                < fetch_priority("Publication of notification under Section 3A"))

    def test_an_intention_outranks_an_ambiguous_lowercase_3a(self):
        # Subjects reading "Section 3a" are mostly competent-authority
        # appointments, which carry no clock. Worth downloading, worth
        # downloading last.
        assert (fetch_priority("Publication of notification under Section 3A")
                < fetch_priority("Publication of notification under Section 3a"))

    def test_ordering_a_mixed_month_puts_declarations_first(self):
        subjects = ["Section 3a appointment", "Section 3A intention", "Section 3D declaration"]
        assert sorted(subjects, key=fetch_priority)[0] == "Section 3D declaration"


class TestAgainstTheRealMonth:
    def test_the_filter_selects_most_but_not_all_of_a_real_month(self):
        # Sanity bounds, not an exact count: if the filter ever matches
        # everything or nothing, it has stopped filtering.
        entries = read_page(catalog_html())
        kept = [e for e in entries if is_land_acquisition(e.subject)]
        assert 0 < len(kept) < len(entries)

    def test_every_row_yields_a_retrievable_document_id(self):
        for entry in read_page(catalog_html()):
            assert entry.doc_id > 0
            assert 2015 <= entry.year <= 2100
