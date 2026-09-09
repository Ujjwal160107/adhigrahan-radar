"""The e-Gazette notification parser, exercised against real government text.

Fixtures are real MoRTH notifications with the owner-name schedules stripped;
`tests/fixtures/egazette/README.md` explains the redaction. Because they are
real, this suite is also the regression gate for gazette layout drift: when the
Press changes the template, a fixture test fails here rather than the pipeline
silently ingesting fewer projects.
"""
import os
from datetime import date

import pytest

from ingest.egazette.parse import parse

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "egazette")


def fixture(doc_id):
    with open(os.path.join(FIXTURES, f"{doc_id}.txt"), encoding="utf-8") as fh:
        return fh.read()


class TestSection:
    """Which statutory section a notification was issued under decides
    everything downstream: a 3A starts the clock, a 3D stops it, and a
    section 3(a) appointment is neither."""

    def test_declaration_is_classified_as_3d(self):
        assert parse(fixture(265102), doc_id=265102).section == "3D"

    def test_intention_to_acquire_is_classified_as_3a(self):
        assert parse(fixture(265109), doc_id=265109).section == "3A"

    def test_competent_authority_appointment_is_classified_as_section_3(self):
        # The search catalog calls this one "Section 3a". It is not an
        # acquisition notification, and trusting the catalog would corrupt
        # the corpus with rows that have no clock.
        assert parse(fixture(265086), doc_id=265086).section == "3"


class TestDatedIdentity:
    """The notification date is the clock event. Get it wrong and every
    interval computed from it is wrong."""

    def test_reads_the_english_notification_date(self):
        # The document carries the same date twice, once in Devanagari and
        # once in English. Only the English form is parsed.
        assert parse(fixture(265102), doc_id=265102).notified_on == date(2025, 7, 29)

    def test_reads_the_statutory_order_number(self):
        assert parse(fixture(265102), doc_id=265102).so_number == "S.O. 3514(E)"

    def test_order_number_is_this_notifications_own_not_a_recited_one(self):
        # 265102's file-number trailer reads "[F. No. S.O. 3771(E)3D]" - a
        # different S.O. number entirely. The operative paragraph's number
        # is the notification's own identity.
        assert parse(fixture(265102), doc_id=265102).so_number != "S.O. 3771(E)"


class TestParentNotification:
    """A 3D declaration recites the 3A notification it closes, and dates it.
    That single sentence is what makes a real label possible: 3A date to 3D
    date is the s.3D(3) 365-day clock, from one document, with no joining.

    The three fixtures below are the same field written three different ways
    in three real notifications published within weeks of each other. This is
    not hypothetical variance."""

    def test_reads_parent_date_written_as_bare_number_slash_date(self):
        # "...and Highways, 5527 Dated:23/12/2024 , published in..."
        assert parse(fixture(265102), doc_id=265102).parent_notified_on == date(2024, 12, 23)

    def test_reads_parent_date_written_as_so_with_dotted_date(self):
        # "...and Highways, S.O.3105 (E).Dated: 02.08.2024, published in..."
        assert parse(fixture(265157), doc_id=265157).parent_notified_on == date(2024, 8, 2)

    def test_reads_parent_date_written_as_so_no_with_spaced_date(self):
        # "...and Highways, S.O.No. 3219(E) dated 08.08.2024, published in..."
        assert parse(fixture(265154), doc_id=265154).parent_notified_on == date(2024, 8, 8)

    def test_an_intention_to_acquire_has_no_parent(self):
        # A 3A starts the clock; there is nothing before it to recite.
        assert parse(fixture(265109), doc_id=265109).parent_notified_on is None

    def test_parent_date_precedes_the_notification_that_recites_it(self):
        # A declaration cannot close a clock that had not started. If this
        # ever fails, the regex has matched the wrong date in the paragraph.
        for doc_id in (265102, 265157, 265154):
            n = parse(fixture(doc_id), doc_id=doc_id)
            assert n.parent_notified_on < n.notified_on, doc_id


class TestHighway:
    """Which highway, read from the operative clause only."""

    def test_reads_the_highway_number(self):
        assert parse(fixture(265102), doc_id=265102).nh_no == "NH66"

    def test_ignores_the_ministry_file_number_series(self):
        # 265088's file-number trailer is "RW/JAI/RJ/CE-RO/NH-12012/2025/01/3A".
        # NH-12012 is a MoRTH file series, not a highway. Scanning the whole
        # document for /NH-?\d+/ picks it up and invents a highway that does
        # not exist; only the operative clause is authoritative.
        assert parse(fixture(265088), doc_id=265088).nh_no == "NH23"

    def test_prefers_the_current_number_when_a_highway_was_renumbered(self):
        # "National Highways No.17 (New NH 66)" - the parenthetical restates
        # the current designation, which is the one every other source uses.
        assert parse(fixture(265089), doc_id=265089).nh_no == "NH66"

    def test_a_greenfield_project_has_no_highway_number(self):
        # Not every acquisition is on a numbered highway. This must be None
        # rather than a guess, and nh_no is therefore never a join key.
        assert parse(fixture(265103), doc_id=265103).nh_no is None


class TestStretch:
    """The chainage range, which is what distinguishes two acquisitions on
    the same highway in the same district."""

    def test_reads_the_chainage_range(self):
        n = parse(fixture(265102), doc_id=265102)
        assert (n.km_from, n.km_to) == (417.0, 460.7)

    def test_reads_chainage_split_by_interleaved_page_furniture(self):
        # 265103's operative sentence is interrupted mid-phrase by a running
        # header: "operation of Greenfield 20 THE GAZETTE OF INDIA :
        # EXTRAORDINARY [PART II-SEC. 3(ii)] Highway in the stretch of land
        # from Km. 83.4 to Km. 93.8". Page furniture must be stripped before
        # anything is matched, or sentences silently lose their middle.
        n = parse(fixture(265103), doc_id=265103)
        assert (n.km_from, n.km_to) == (83.4, 93.8)


class TestPlaces:
    """Where the land is. These feed the district selection and, later, the
    project-to-parcel binding in s10."""

    def test_reads_the_state(self):
        assert parse(fixture(265102), doc_id=265102).state == "KERALA"

    def test_the_state_stops_before_the_district_on_a_shared_line(self):
        # Most schedules pad "State: X" and "District: Y" apart with a run of
        # spaces; some use a single space. Stopping only at the padding
        # produced states called "HIMACHAL PRADESH District: SHIMLA", which
        # then became their own row in every state-level aggregate.
        assert parse("State: HIMACHAL PRADESH District: SHIMLA\n"
                     "S.O. 1(E).—conferred by sub-section (1) of section 3A",
                     doc_id=1).state == "HIMACHAL PRADESH"

    def test_reads_the_districts(self):
        assert parse(fixture(265109), doc_id=265109).districts == ("AGAR MALWA",)

    def test_the_district_is_read_from_a_single_spaced_shared_line(self):
        assert parse("State: HIMACHAL PRADESH District: SHIMLA\n"
                     "S.O. 1(E).—conferred by sub-section (1) of section 3A",
                     doc_id=1).districts == ("SHIMLA",)

    def test_reads_every_village_in_document_order(self):
        villages = parse(fixture(265102), doc_id=265102).villages
        assert villages[0] == "Ambalappuzha North Block 14"
        assert len(villages) == 18

    def test_a_section_3_appointment_names_no_villages_to_acquire(self):
        assert parse(fixture(265086), doc_id=265086).villages == ()


ALL_FIXTURES = sorted(
    int(f[:-4]) for f in os.listdir(FIXTURES) if f.endswith(".txt"))

# The Kerala schedule layout, with the owner column filled by placeholders.
# The real one names private individuals and is redacted out of the fixtures,
# so the layout is reproduced here rather than reproducing the people.
KERALA_LAYOUT = """
          S.O. 3514(E).—Now, therefore, in exercise of the powers conferred by the
sub-section (1) of section 3D of the said Act, the Central Government hereby declares.
                                                    SCHEDULE
State: KERALA                                   District: ALAPPUZHA
Sl. No.       Survey/Plot       Type of Land      Nature of      Area      Name of Land Owner
Village: Ambalappuzha North Block 14
1          212/12               Private         Dry            0.0039 FIRST OWNER
2          248/2                Private         Dry            0.0014 SECOND OWNER
3          248/25               Private         Dry            0.0010 THIRD OWNER
"""


class TestArea:
    """How much land is being taken.

    `area_hectares` is one of the twenty model features, so it cannot be left
    null for real rows, and it must not be derived from the chainage either -
    a computed area presented as a measured one is exactly the sort of
    invented figure the project's honesty rules forbid. It is summed from the
    schedule, which states it.

    The schedule comes in two layouts. Madhya Pradesh prints "Area (in Local
    Unit)" and "Area (in Hectares)" as separate columns; Kerala prints a
    single "Area (in Hectares)" followed by the owner's name. The rightmost
    decimal figure on a row is the hectares in both.
    """

    def test_sums_the_hectares_column(self):
        # 265109's rows read "1  1150  Government  Non Irrigated
        # 0.42596 (Hectare)  0.42596" - two area columns, the second being
        # the one in hectares. Over the 60 rows this fixture keeps.
        assert parse(fixture(265109), doc_id=265109).area_hectares == pytest.approx(19.61343)

    def test_reads_the_layout_where_a_name_follows_the_area(self):
        # The rule must not depend on the area being the last thing on the
        # line, because in this layout it is not.
        assert parse(KERALA_LAYOUT, doc_id=1).area_hectares == pytest.approx(0.0063)

    def test_a_notification_with_no_schedule_rows_has_no_area(self):
        # A §3 competent-authority appointment schedules no land. None, not
        # zero: zero is a measurement, and absence is not.
        assert parse(fixture(265086), doc_id=265086).area_hectares is None

    @pytest.mark.parametrize("doc_id", ALL_FIXTURES)
    def test_area_is_positive_where_it_is_stated_at_all(self, doc_id):
        area = parse(fixture(doc_id), doc_id=doc_id).area_hectares
        assert area is None or area > 0


class TestWholeCorpus:
    """Invariants over every fixture. These are the tests that catch gazette
    layout drift: a template change at the Press shows up here as a failure
    rather than as a quietly smaller corpus."""

    def test_every_fixture_parses(self):
        assert ALL_FIXTURES, "fixtures directory is empty"
        unparsed = [d for d in ALL_FIXTURES if parse(fixture(d), doc_id=d) is None]
        assert unparsed == []

    @pytest.mark.parametrize("doc_id", ALL_FIXTURES)
    def test_every_notification_is_dated(self, doc_id):
        # An undated notification is useless: the date *is* the clock event.
        assert parse(fixture(doc_id), doc_id=doc_id).notified_on is not None

    @pytest.mark.parametrize("doc_id", ALL_FIXTURES)
    def test_only_a_declaration_recites_a_parent(self, doc_id):
        # A §3A starts the clock and a §3 appoints an officer; neither has a
        # predecessor to close. If one acquires a parent date, the recital
        # regex has escaped its span.
        n = parse(fixture(doc_id), doc_id=doc_id)
        if n.section != "3D":
            assert n.parent_notified_on is None

    @pytest.mark.parametrize("doc_id", ALL_FIXTURES)
    def test_chainage_runs_forwards(self, doc_id):
        n = parse(fixture(doc_id), doc_id=doc_id)
        if n.km_from is not None:
            assert n.km_to > n.km_from

    @pytest.mark.parametrize("doc_id", ALL_FIXTURES)
    def test_no_village_is_a_schedule_column_header(self, doc_id):
        # The schedule's own header row starts with "Sl. No." and sits right
        # above the village rows. Picking it up would inject a fake village
        # into the corpus, and it would look plausible in a report.
        for village in parse(fixture(doc_id), doc_id=doc_id).villages:
            assert not village.lower().startswith(("sl.", "survey", "type of"))
