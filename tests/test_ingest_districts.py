"""Choosing which districts the corpus covers.

The districts are picked by the data rather than declared up front. Naming
eight in advance risks naming eight with no notifications in them, and the
gazette does not distribute acquisitions evenly - one corridor project can
dominate a state's whole year.
"""
from ingest.districts import select


def notifications(state, district, count):
    return [{"state": state, "districts": [district]} for _ in range(count)]


class TestRanking:
    def test_keeps_the_busiest_districts(self):
        rows = (notifications("UTTAR PRADESH", "GONDA", 5)
                + notifications("UTTAR PRADESH", "BASTI", 3)
                + notifications("UTTAR PRADESH", "AGRA", 1))

        assert select(rows, per_state=2) == {"UTTAR PRADESH": ["GONDA", "BASTI"]}

    def test_ranks_each_state_independently(self):
        # Bihar's busiest district must not be crowded out by Uttar Pradesh
        # having more notifications overall.
        rows = (notifications("UTTAR PRADESH", "GONDA", 50)
                + notifications("BIHAR", "PATNA", 2))

        chosen = select(rows, per_state=1)

        assert chosen == {"UTTAR PRADESH": ["GONDA"], "BIHAR": ["PATNA"]}

    def test_a_notification_spanning_districts_counts_for_each(self):
        rows = [{"state": "BIHAR", "districts": ["PATNA", "NALANDA"]}]
        assert set(select(rows, per_state=2)["BIHAR"]) == {"PATNA", "NALANDA"}

    def test_ties_break_alphabetically_so_the_result_is_reproducible(self):
        rows = notifications("BIHAR", "SARAN", 2) + notifications("BIHAR", "ARARIA", 2)
        assert select(rows, per_state=1) == {"BIHAR": ["ARARIA"]}


class TestForcedInclusions:
    def test_a_forced_district_is_kept_even_when_it_ranks_low(self):
        # Sultanpur is the only district with a real litigation corpus, and
        # the flagship project binds to a parcel in it. Losing it to a
        # ranking would silently disconnect the two halves of the product.
        rows = (notifications("UTTAR PRADESH", "GONDA", 90)
                + notifications("UTTAR PRADESH", "SULTANPUR", 1))

        chosen = select(rows, per_state=1, always_include=("SULTANPUR",))

        assert "SULTANPUR" in chosen["UTTAR PRADESH"]

    def test_a_forced_district_does_not_shrink_the_quota(self):
        rows = (notifications("UTTAR PRADESH", "GONDA", 90)
                + notifications("UTTAR PRADESH", "SULTANPUR", 1))

        chosen = select(rows, per_state=2, always_include=("SULTANPUR",))

        assert sorted(chosen["UTTAR PRADESH"]) == ["GONDA", "SULTANPUR"]

    def test_a_forced_district_absent_from_the_data_is_not_invented(self):
        # Forcing in a district with no notifications would put an empty
        # district in the corpus and make every feature there null.
        rows = notifications("UTTAR PRADESH", "GONDA", 5)
        assert select(rows, per_state=2, always_include=("SULTANPUR",)) == {
            "UTTAR PRADESH": ["GONDA"]}


class TestEdges:
    def test_no_notifications_selects_no_districts(self):
        assert select([], per_state=8) == {}

    def test_notifications_with_no_district_are_ignored(self):
        # A §3 competent-authority appointment names no districts in the
        # form the parser reads.
        assert select([{"state": "BIHAR", "districts": []}], per_state=8) == {}

    def test_fewer_districts_than_the_quota_is_not_padded(self):
        rows = notifications("BIHAR", "PATNA", 3)
        assert select(rows, per_state=8) == {"BIHAR": ["PATNA"]}
