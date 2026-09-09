"""Bhoomi Rashi's dropdown response format.

Neither JSON nor HTML, with no specification anywhere: `id|$|Name|~|id|$|Name`.
The only way it is pinned is here, against real responses captured from the
live endpoints on 2026-09-10.
"""
from ingest.bhoomirashi.gazetteer import parse_options

# Real, truncated: GET filldist1.cshtml?val=9 (Uttar Pradesh).
UP_DISTRICTS = (
    "121|$| AMBEDKAR NAGAR |~|118|$|AGRA |~|640|$|Amethi |~|140|$|Ayodhya|~|"
    "129|$|BARABANKI |~|131|$|BASTI |~|147|$|GONDA |~|174|$|PRATAPGARH |~|"
    "175|$|RAEBARELI |~|185|$|SULTANPUR "
)
# Real: GET fillplace1.cshtml?val=185 (Sultanpur).
SULTANPUR_TEHSILS = (
    "9850|$|Baldirai|~|916|$|Jaisinghpur |~|918|$|Kadipur |~|917|$|Lambhuwa |~|915|$|Sadar"
)


class TestParsing:
    def test_reads_every_option(self):
        assert len(parse_options(SULTANPUR_TEHSILS)) == 5

    def test_maps_the_portal_id_to_the_name(self):
        assert parse_options(UP_DISTRICTS)["185"] == "SULTANPUR"

    def test_strips_the_padding_the_portal_emits(self):
        # The portal pads names with leading and trailing spaces
        # inconsistently (" AMBEDKAR NAGAR ", "Ayodhya"). Left alone, the
        # padding reaches every name comparison downstream.
        assert parse_options(UP_DISTRICTS)["121"] == "AMBEDKAR NAGAR"

    def test_drops_the_placeholder_option(self):
        # "-1" is the "-- DISTRICT --" prompt, not a district.
        assert "-1" not in parse_options("-1|$|-- DISTRICT --|~|185|$|SULTANPUR")

    def test_an_empty_response_yields_nothing(self):
        assert parse_options("") == {}

    def test_a_malformed_chunk_is_skipped_not_fatal(self):
        # A truncated response must cost the chunks it truncated, not the
        # whole harvest.
        assert parse_options("185|$|SULTANPUR|~|garbage|~|640|$|Amethi") == {
            "185": "SULTANPUR", "640": "Amethi"}


class TestTargetDistrictsAreReachable:
    def test_all_eight_existing_uttar_pradesh_districts_are_present(self):
        # These are the districts the build already targets. If the portal
        # ever renames or drops one, the mapping breaks silently, so it is
        # asserted rather than assumed.
        names = {n.upper() for n in parse_options(UP_DISTRICTS).values()}
        for district in ("SULTANPUR", "AMETHI", "PRATAPGARH", "RAEBARELI",
                         "AYODHYA", "BARABANKI", "GONDA", "BASTI"):
            assert district in names
