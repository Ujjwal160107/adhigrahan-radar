"""What the harvest does when it is interrupted, repeated, or run twice at once.

The rest of the ingest suite tests what the layer computes. This one tests
what survives - a different question, and the one the existing tests could
not answer, because every one of them runs the happy path and none of them
stops a write half way.

Each class below pins a property that used to be false:

* an interruption mid-write could leave a JSON file truncated, and a
  truncated `index.json` raised inside `RawStore.__init__`, so *every* entry
  point died - including `--summarise-only`, the documented recovery;
* one unreadable notification file made the whole contract unpublishable;
* a project changed identity whenever a later document about it arrived;
* a rejection was permanent, because the bytes behind it were discarded;
* an unreadable PDF was re-downloaded on every harvest, for ever;
* two concurrent harvests silently erased each other's record.
"""
import json
import os
from datetime import date
from types import SimpleNamespace

import pytest

from ingest import config
from ingest import refresh as refresh_module
from ingest import store as store_module
from ingest.egazette import documents
from ingest.egazette.parse import PARSER_VERSION
from ingest.egazette.records import Notification
from ingest.projects import assemble
from ingest.store import KEPT, REJECTED, UNAVAILABLE, Lock, LockHeld, RawStore, write_atomic


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """A store whose published contract lands inside `tmp_path`."""
    monkeypatch.setattr(config, "RAW", str(tmp_path))
    monkeypatch.setattr(refresh_module, "PROJECTS",
                        os.path.join(str(tmp_path), "acquisition_projects.json"))
    monkeypatch.setattr(refresh_module, "TARGET_DISTRICTS",
                        os.path.join(str(tmp_path), "target_districts.json"))
    return tmp_path


def journal_of(root):
    return os.path.join(str(root), "egazette", "journal.ndjson")


def quiet(*_args, **_kwargs):
    pass


class TestAtomicWrites:
    """A reader sees the old file or the new one, never half of either."""

    def test_a_crash_before_the_rename_leaves_the_previous_file_intact(self, tmp_path,
                                                                       monkeypatch):
        path = str(tmp_path / "contract.json")
        write_atomic(path, b'{"generation": 1}')

        def die(*_):
            raise OSError("killed between the write and the rename")

        monkeypatch.setattr(store_module.os, "replace", die)
        with pytest.raises(OSError):
            write_atomic(path, b'{"generation": 2}')

        with open(path, encoding="utf-8") as fh:
            assert json.load(fh) == {"generation": 1}

    def test_it_leaves_no_partial_file_behind(self, tmp_path, monkeypatch):
        def die(*_):
            raise OSError("killed between the write and the rename")

        monkeypatch.setattr(store_module.os, "replace", die)
        with pytest.raises(OSError):
            write_atomic(str(tmp_path / "contract.json"), b"{}")

        assert [n for n in os.listdir(tmp_path) if n.startswith(".tmp-")] == []


class TestSurvivingCorruption:
    """The store opens, and the contract publishes, whatever is damaged."""

    def test_a_damaged_index_cache_is_rebuilt_from_the_journal(self, tmp_path):
        # This is the one that used to raise JSONDecodeError inside the
        # constructor and take every entry point down with it.
        RawStore(tmp_path).record(265102, kept=True)
        (tmp_path / "egazette" / "index.json").write_text('{"265102": {"kep',
                                                          encoding="utf-8")

        resumed = RawStore(tmp_path)

        assert resumed.kept(265102)

    def test_a_torn_final_journal_line_is_discarded(self, tmp_path):
        store = RawStore(tmp_path)
        store.record(265102, kept=True)
        store.record(265154, kept=False, reason="user fee")
        with open(journal_of(tmp_path), "a", encoding="utf-8") as fh:
            fh.write('{"doc_id": 265157, "outcome": "ke')     # killed mid-append

        resumed = RawStore(tmp_path)

        assert resumed.kept(265102)
        assert resumed.has_seen(265154)
        # The decision that was never finished was never made.
        assert not resumed.has_seen(265157)

    def test_one_unreadable_notification_does_not_hide_the_others(self, tmp_path):
        store = RawStore(tmp_path)
        store.put_notification(265102, {"doc_id": 265102, "section": "3D"})
        store.put_notification(265154, {"doc_id": 265154, "section": "3A"})
        (tmp_path / "egazette" / "notifications" / "265154.json").write_text(
            '{"doc_id": 265154, "sec', encoding="utf-8")

        rows = RawStore(tmp_path).read_notifications()

        assert [r["doc_id"] for r in rows] == [265102]

    def test_it_names_the_notification_it_could_not_read(self, tmp_path):
        # Skipped, but never silently: a file that will not parse is a
        # document missing from the published contract.
        store = RawStore(tmp_path)
        store.put_notification(265102, {"doc_id": 265102, "section": "3D"})
        (tmp_path / "egazette" / "notifications" / "265102.json").write_text(
            "{", encoding="utf-8")

        store.read_notifications()

        assert store.damaged == ["265102.json"]

    def test_the_contract_still_publishes_around_a_damaged_file(self, mirror):
        store = RawStore(mirror)
        store.put_notification(265102, {
            "doc_id": 265102, "section": "3D", "notified_on": "2025-07-29",
            "parent_notified_on": "2024-12-23", "state": "KERALA",
            "districts": ["ALAPPUZHA"]})
        (mirror / "egazette" / "notifications" / "999.json").write_text(
            "not json at all", encoding="utf-8")

        report = refresh_module.refresh(summarise_only=True, log=quiet)

        assert report["projects"] == 1
        assert report["damaged_notifications"] == ["999.json"]


class TestRepeatingARun:
    """Running the same thing twice does the work once."""

    def test_a_settled_document_is_never_fetched_again(self, tmp_path):
        store = RawStore(tmp_path)
        for doc_id, outcome in ((1, KEPT), (2, REJECTED), (3, UNAVAILABLE)):
            store.record(doc_id, outcome=outcome)
            assert not store.should_fetch(doc_id)

    def test_a_transient_failure_stays_eligible(self, tmp_path):
        # The whole point of not recording it: one bad afternoon must not
        # permanently lose a project.
        store = RawStore(tmp_path)
        store.defer(900, reason="503 from egazette.gov.in")

        assert store.should_fetch(900)
        assert store.attempts(900) == 1

    def test_republishing_an_unchanged_mirror_rewrites_nothing(self, mirror):
        store = RawStore(mirror)
        store.put_notification(265102, {
            "doc_id": 265102, "section": "3D", "notified_on": "2025-07-29",
            "parent_notified_on": "2024-12-23", "state": "KERALA",
            "districts": ["ALAPPUZHA"]})
        refresh_module.refresh(summarise_only=True, log=quiet)

        contract = mirror / "acquisition_projects.json"
        before = (contract.read_bytes(), contract.stat().st_mtime_ns)
        refresh_module.refresh(summarise_only=True, log=quiet)

        # Identical bytes is not enough. A rewrite producing the same content
        # would still tell everything watching the file - a build keyed on
        # modification time, an operator reading `git status` - that a run
        # which did nothing had changed something.
        assert (contract.read_bytes(), contract.stat().st_mtime_ns) == before


class TestStableProjectIdentity:
    """A project keeps one identity for its whole life.

    Downstream, `project_id` is a foreign key: a risk score, a parcel
    binding, an officer's watchlist entry. Anchoring it on the newest
    document meant three harvests of a strictly growing corpus produced three
    different ids for one physical acquisition, and every reference broke
    each time.
    """

    @staticmethod
    def notification(doc_id, section, on, parent=None):
        return Notification(
            doc_id=doc_id, section=section, notified_on=date.fromisoformat(on),
            parent_notified_on=date.fromisoformat(parent) if parent else None,
            nh_no="NH160A", km_from=10.0, km_to=20.0,
            state="MAHARASHTRA", districts=("Nashik",))

    def harvests(self):
        first = [self.notification(100, "3A", "2025-01-10")]
        second = first + [self.notification(200, "3A", "2025-06-10")]
        third = second + [self.notification(300, "3D", "2026-05-10", parent="2025-06-10")]
        return first, second, third

    def test_the_id_survives_a_republished_intention(self):
        first, second, _ = self.harvests()
        assert ([p.project_id for p in assemble(first)]
                == [p.project_id for p in assemble(second)])

    def test_the_id_survives_the_declaration_that_closes_it(self):
        first, _, third = self.harvests()
        assert ([p.project_id for p in assemble(first)]
                == [p.project_id for p in assemble(third)])

    def test_the_clock_still_runs_from_the_latest_intention(self):
        # Identity anchors on the earliest document; the dates must still
        # come from the latest, or a republication would report a breach
        # against a notification that has been superseded.
        _, second, _ = self.harvests()
        (project,) = assemble(second)
        assert project.notified_3a_on == date(2025, 6, 10)

    def test_a_superseded_intention_does_not_reappear_as_its_own_project(self):
        # A §3D recites only the §3A it closes - the latest one. Testing each
        # §3A separately left the superseded republication looking unclaimed,
        # so it came back as a second, open, permanently unclosable project
        # for an acquisition that had already finished.
        _, _, third = self.harvests()
        assert len(assemble(third)) == 1

    def test_the_closed_project_keeps_every_source_document(self):
        _, _, third = self.harvests()
        (project,) = assemble(third)
        assert project.source_doc_ids == (100, 200, 300)
        assert project.republication_count == 1


class TestRevisableRejections:
    """An improved parser can reach a document it once turned away."""

    def test_a_rejection_keeps_the_text_it_was_decided_from(self, tmp_path):
        store = RawStore(tmp_path)
        store.put_text(900, "the operative clause", evidence=True)
        assert store.read_text(900) == "the operative clause"

    def test_the_evidence_for_a_rejection_is_committed_beside_the_mirror(self, tmp_path):
        # Under `rejected/` rather than `text/`, because .gitignore keeps the
        # kept corpus's text out of the repository and this text is the one
        # thing a clone cannot reconstruct - the PDF was never stored.
        RawStore(tmp_path).put_text(900, "clause", evidence=True)
        assert (tmp_path / "egazette" / "rejected" / "900.txt.gz").exists()

    def test_a_parser_bump_makes_a_rejection_stale_again(self, tmp_path):
        store = RawStore(tmp_path)
        store.put_text(900, "clause", evidence=True)
        store.record(900, outcome=REJECTED, reason="section 3", parser_version="2020.01.1")

        assert store.stale(PARSER_VERSION) == [900]

    def test_a_rejection_with_no_retained_text_is_reported_not_silently_skipped(
            self, tmp_path):
        # The pre-journal rejections. They are the only documents an improved
        # parser genuinely cannot reach without the network, and saying so is
        # the difference between a limitation and a bug.
        store = RawStore(tmp_path)
        store.record(900, outcome=REJECTED, reason="section 3", parser_version="2020.01.1")

        assert store.stale(PARSER_VERSION) == []
        assert store.undecidable_rejections(PARSER_VERSION) == [900]

    def test_reopening_makes_it_eligible_for_the_next_harvest(self, tmp_path):
        store = RawStore(tmp_path)
        store.record(900, outcome=REJECTED, reason="section 3")
        assert not store.should_fetch(900)

        store.reopen(900, reason="parser improved")

        assert store.should_fetch(900)

    def test_reparse_recovers_a_document_the_new_parser_accepts(self, mirror, monkeypatch):
        store = RawStore(mirror)
        store.put_text(265102, "text the old parser could not read", evidence=True)
        store.record(265102, outcome=REJECTED, reason="section 3, no acquisition clock",
                     parser_version="2020.01.1")
        monkeypatch.setattr(refresh_module, "parse", lambda text, doc_id: Notification(
            doc_id=doc_id, section="3D", notified_on=date(2025, 7, 29),
            parent_notified_on=date(2024, 12, 23), state="KERALA",
            districts=("ALAPPUZHA",)))

        report = refresh_module.refresh(reparse=True, log=quiet)

        assert report["recovered"] == 1
        assert RawStore(mirror).kept(265102)
        assert report["projects"] == 1

    def test_reparsing_twice_does_the_work_once(self, mirror, monkeypatch):
        store = RawStore(mirror)
        store.put_text(265102, "text", evidence=True)
        store.record(265102, outcome=REJECTED, reason="section 3",
                     parser_version="2020.01.1")
        monkeypatch.setattr(refresh_module, "parse", lambda text, doc_id: Notification(
            doc_id=doc_id, section="3D", notified_on=date(2025, 7, 29),
            parent_notified_on=date(2024, 12, 23), state="KERALA",
            districts=("ALAPPUZHA",)))

        refresh_module.refresh(reparse=True, log=quiet)
        second = refresh_module.refresh(reparse=True, log=quiet)

        assert second["reparsed"] == 0


class TestUnreadableDocuments:
    """A document that cannot be read must stop costing fetches."""

    entry = SimpleNamespace(doc_id=900, year=2025)

    def test_a_scan_is_settled_at_once(self, tmp_path):
        # A PDF that opens cleanly and has no text layer is a scan.
        # Re-downloading it can only produce the same scan.
        store = RawStore(tmp_path)

        outcome = refresh_module._unreadable(store, self.entry, "abc123",
                                             documents.NO_TEXT_LAYER)

        assert outcome == "skipped"
        assert not store.should_fetch(900)

    def test_bytes_that_are_not_a_pdf_are_retried(self, tmp_path):
        # Usually a truncated response from a slow host, which really will
        # serve correctly next time.
        store = RawStore(tmp_path)

        outcome = refresh_module._unreadable(store, self.entry, "abc123",
                                             documents.NOT_A_PDF)

        assert outcome == "failed"
        assert store.should_fetch(900)

    def test_but_not_for_ever(self, tmp_path):
        store = RawStore(tmp_path)
        for _ in range(refresh_module.MAX_UNREADABLE_ATTEMPTS - 1):
            refresh_module._unreadable(store, self.entry, "abc123", documents.NOT_A_PDF)

        outcome = refresh_module._unreadable(store, self.entry, "abc123",
                                             documents.NOT_A_PDF)

        assert outcome == "skipped"
        assert not store.should_fetch(900)


class TestOneWriterAtATime:
    def test_a_second_harvest_is_refused(self, tmp_path):
        path = tmp_path / ".lock"
        Lock(path, "run-a").acquire()

        with pytest.raises(LockHeld):
            Lock(path, "run-b").acquire()

    def test_the_refusal_names_the_run_holding_the_store(self, tmp_path):
        path = tmp_path / ".lock"
        Lock(path, "run-a").acquire()

        with pytest.raises(LockHeld) as raised:
            Lock(path, "run-b").acquire()

        assert "run-a" in str(raised.value)

    def test_releasing_lets_the_next_run_in(self, tmp_path):
        path = tmp_path / ".lock"
        Lock(path, "run-a").acquire().release()

        Lock(path, "run-b").acquire().release()     # must not raise

    def test_a_stale_lock_can_be_taken_deliberately(self, tmp_path):
        # Never by age. A lock that expires on its own lets a second writer
        # in exactly when the first is slow, which is when it hurts most.
        path = tmp_path / ".lock"
        Lock(path, "run-a").acquire()

        Lock(path, "run-b").acquire(force=True).release()


class TestCorpusFingerprint:
    def test_it_does_not_depend_on_the_order_documents_arrived(self, tmp_path):
        one, two = RawStore(tmp_path / "one"), RawStore(tmp_path / "two")
        for doc_id in (1, 2, 3):
            one.record(doc_id, outcome=KEPT, content_sha256=f"hash{doc_id}",
                       parser_version=PARSER_VERSION)
        for doc_id in (3, 1, 2):
            two.record(doc_id, outcome=KEPT, content_sha256=f"hash{doc_id}",
                       parser_version=PARSER_VERSION)

        assert one.corpus_fingerprint() == two.corpus_fingerprint()

    def test_it_changes_when_a_document_changes(self, tmp_path):
        store = RawStore(tmp_path)
        store.record(1, outcome=KEPT, content_sha256="hash1",
                     parser_version=PARSER_VERSION)
        before = store.corpus_fingerprint()

        store.record(1, outcome=KEPT, content_sha256="corrigendum",
                     parser_version=PARSER_VERSION)

        assert store.corpus_fingerprint() != before

    def test_it_changes_when_the_parser_changes(self, tmp_path):
        store = RawStore(tmp_path)
        store.record(1, outcome=KEPT, content_sha256="hash1", parser_version="2020.01.1")
        before = store.corpus_fingerprint()

        store.record(1, outcome=KEPT, content_sha256="hash1",
                     parser_version=PARSER_VERSION)

        assert store.corpus_fingerprint() != before

    def test_a_rejected_document_is_not_part_of_the_corpus(self, tmp_path):
        store = RawStore(tmp_path)
        store.record(1, outcome=KEPT, content_sha256="hash1",
                     parser_version=PARSER_VERSION)
        before = store.corpus_fingerprint()

        store.record(2, outcome=REJECTED, reason="section 3",
                     parser_version=PARSER_VERSION)

        assert store.corpus_fingerprint() == before


class TestMigratingAnOlderMirror:
    """The mirror in this repository predates the journal. Not one decision
    in it may be lost, least of all the rejections - those are the documents
    that would otherwise be downloaded all over again."""

    def test_every_decision_in_a_legacy_index_is_imported(self, tmp_path):
        base = tmp_path / "egazette"
        base.mkdir(parents=True)
        (base / "index.json").write_text(json.dumps({
            "265102": {"kept": True, "reason": None},
            "265154": {"kept": False, "reason": "section 3, no acquisition clock"},
        }), encoding="utf-8")

        store = RawStore(tmp_path)

        assert store.kept(265102)
        assert store.has_seen(265154)
        assert store.decision(265154)["reason"] == "section 3, no acquisition clock"

    def test_it_imports_once_and_not_again(self, tmp_path):
        base = tmp_path / "egazette"
        base.mkdir(parents=True)
        (base / "index.json").write_text(
            json.dumps({"265102": {"kept": True, "reason": None}}), encoding="utf-8")

        RawStore(tmp_path)
        lines = (base / "journal.ndjson").read_text(encoding="utf-8").splitlines()
        RawStore(tmp_path)

        assert (base / "journal.ndjson").read_text(encoding="utf-8").splitlines() == lines
