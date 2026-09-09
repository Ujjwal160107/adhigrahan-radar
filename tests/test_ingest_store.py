"""The on-disk mirror of what has been harvested.

`data/raw/` is the boundary between the network and the build. Everything the
ingest layer retrieves lands here, and the offline build reads only from
here - so a rebuild is deterministic and the demo never touches a government
server.

The store also remembers what it has *already looked at*, including documents
it decided not to keep. Without that, every refresh would re-download the
same rejected notifications forever, and a harvest could never be resumed
after an interruption.
"""
import json

import pytest

from ingest.store import RawStore


@pytest.fixture
def store(tmp_path):
    return RawStore(tmp_path)


class TestDocuments:
    def test_a_stored_document_is_readable_back(self, store):
        store.put_document(265102, 2025, b"%PDF-1.6 fake")
        assert store.read_document(265102) == b"%PDF-1.6 fake"

    def test_documents_are_partitioned_by_year_under_pdf(self, tmp_path):
        # Two things live under data/raw/, which is a committed directory:
        # the parsed notifications, which are small and belong in the repo so
        # the demo needs no network, and the source PDFs, which are megabytes
        # each and do not. Keeping the PDFs under their own prefix is what
        # lets .gitignore separate them with one line.
        RawStore(tmp_path).put_document(265102, 2025, b"x")
        assert (tmp_path / "egazette" / "pdf" / "2025" / "265102.pdf").exists()

    def test_an_absent_document_reads_as_none(self, store):
        assert store.read_document(999999) is None

    def test_holds_reports_what_is_already_downloaded(self, store):
        store.put_document(265102, 2025, b"x")
        assert store.holds(265102)
        assert not store.holds(265103)


class TestSeenIndex:
    def test_a_document_is_not_seen_before_it_is_recorded(self, store):
        assert not store.has_seen(265102)

    def test_recording_a_skip_marks_it_seen(self, store):
        # The point of recording a rejection: a refresh must not spend a
        # fetch re-deciding something it already decided.
        store.record(265102, kept=False, reason="not a land acquisition")
        assert store.has_seen(265102)
        assert not store.holds(265102)

    def test_the_index_survives_a_new_store_over_the_same_directory(self, tmp_path):
        RawStore(tmp_path).record(265102, kept=False, reason="user fee")
        assert RawStore(tmp_path).has_seen(265102)

    def test_the_index_records_why_something_was_skipped(self, tmp_path):
        # A harvest that quietly drops documents is impossible to audit. The
        # reason is what makes a low yield explainable rather than suspicious.
        RawStore(tmp_path).record(265102, kept=False, reason="user fee")
        index = json.loads((tmp_path / "egazette" / "index.json").read_text())
        assert index["265102"]["reason"] == "user fee"

    def test_a_later_record_supersedes_an_earlier_one(self, store):
        store.record(265102, kept=False, reason="transient fetch failure")
        store.record(265102, kept=True, reason=None)
        assert store.kept(265102)


class TestNotifications:
    def test_a_stored_notification_is_readable_back(self, store):
        store.put_notification(265102, {"section": "3D", "doc_id": 265102})
        assert store.read_notifications() == [{"section": "3D", "doc_id": 265102}]

    def test_notifications_come_back_in_a_stable_order(self, store):
        for doc_id in (265157, 265102, 265154):
            store.put_notification(doc_id, {"doc_id": doc_id})
        assert [n["doc_id"] for n in store.read_notifications()] == [265102, 265154, 265157]

    def test_an_empty_store_yields_no_notifications(self, store):
        assert store.read_notifications() == []


class TestResumability:
    def test_an_interrupted_harvest_resumes_where_it_stopped(self, tmp_path):
        # Simulates the interruption: two documents handled, then a new
        # process over the same directory. Government hosts are slow and a
        # full harvest is long; restarting from zero is not acceptable.
        first = RawStore(tmp_path)
        first.put_document(265102, 2025, b"x")
        first.record(265102, kept=True, reason=None)
        first.record(265103, kept=False, reason="user fee")

        resumed = RawStore(tmp_path)
        todo = [d for d in (265102, 265103, 265104) if not resumed.has_seen(d)]

        assert todo == [265104]
