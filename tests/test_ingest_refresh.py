"""Publishing the contract the risk engine reads.

A full harvest runs for hours. It will be interrupted - by a stopped
terminal, a dropped link, a laptop lid. If the contract files were only
written at the very end of a complete run, every interrupted harvest would
leave the mirror full of documents and the build with nothing to read.

So publishing is separable from harvesting, and can be done offline against
whatever the mirror already holds.
"""
import json
import os
from datetime import date

import pytest

from ingest import config
from ingest import refresh as refresh_module
from ingest.egazette.parse import parse
from ingest.store import RawStore

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "egazette")


@pytest.fixture
def raw(tmp_path, monkeypatch):
    """A mirror holding three real declarations, and nothing else."""
    monkeypatch.setattr(config, "RAW", str(tmp_path))
    monkeypatch.setattr(refresh_module, "PROJECTS",
                        os.path.join(str(tmp_path), "acquisition_projects.json"))
    monkeypatch.setattr(refresh_module, "TARGET_DISTRICTS",
                        os.path.join(str(tmp_path), "target_districts.json"))
    store = RawStore(tmp_path)
    for doc_id in (265102, 265154, 265157):
        with open(os.path.join(FIXTURES, f"{doc_id}.txt"), encoding="utf-8") as fh:
            notification = parse(fh.read(), doc_id=doc_id)
        store.put_notification(doc_id, _jsonable(notification))
    return tmp_path


def _jsonable(notification):
    from dataclasses import asdict

    row = asdict(notification)
    for key in ("notified_on", "parent_notified_on"):
        if row.get(key):
            row[key] = row[key].isoformat()
    return row


def read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TestPublishingOffline:
    def test_it_publishes_without_touching_the_network(self, raw):
        # No transport, no certificate repair, no session. If this reaches
        # the network the test hangs or fails - which is the assertion.
        report = refresh_module.refresh(summarise_only=True, log=lambda *_: None)
        assert report["projects"] == 3

    def test_it_writes_the_projects_contract(self, raw):
        refresh_module.refresh(summarise_only=True, log=lambda *_: None)
        projects = read(os.path.join(raw, "acquisition_projects.json"))
        assert len(projects) == 3

    def test_it_writes_the_target_districts(self, raw):
        refresh_module.refresh(summarise_only=True, log=lambda *_: None)
        assert read(os.path.join(raw, "target_districts.json"))

    def test_published_projects_carry_real_statutory_intervals(self, raw):
        refresh_module.refresh(summarise_only=True, log=lambda *_: None)
        for project in read(os.path.join(raw, "acquisition_projects.json")):
            started = date.fromisoformat(project["notified_3a_on"])
            declared = date.fromisoformat(project["declared_3d_on"])
            assert 0 < (declared - started).days < 400

    def test_dates_are_published_as_iso_strings(self, raw):
        # They round-trip through JSON, so they must be strings a consumer
        # can parse rather than whatever `str(date)` happened to produce.
        refresh_module.refresh(summarise_only=True, log=lambda *_: None)
        project = read(os.path.join(raw, "acquisition_projects.json"))[0]
        assert date.fromisoformat(project["notified_3a_on"])

    def test_an_empty_mirror_publishes_an_empty_contract(self, tmp_path, monkeypatch):
        # A first run that fetched nothing must leave a valid empty contract,
        # not a missing file the build then fails to open.
        monkeypatch.setattr(config, "RAW", str(tmp_path))
        monkeypatch.setattr(refresh_module, "PROJECTS",
                            os.path.join(str(tmp_path), "acquisition_projects.json"))
        monkeypatch.setattr(refresh_module, "TARGET_DISTRICTS",
                            os.path.join(str(tmp_path), "target_districts.json"))

        refresh_module.refresh(summarise_only=True, log=lambda *_: None)

        assert read(os.path.join(tmp_path, "acquisition_projects.json")) == []


class TestReparsing:
    """The parser improves; the mirror should not have to be re-downloaded.

    Every fix to `parse.py` so far was prompted by real documents - a state
    read as "HIMACHAL PRADESH District: SHIMLA", a chainage lost to an
    interleaved page header. Each time, the notifications already on disk
    carry the old, wrong values. Re-fetching twenty thousand PDFs to correct
    a regex is not an option, and the PDFs are already here.
    """

    def test_it_reparses_stored_documents_without_the_network(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "RAW", str(tmp_path))
        monkeypatch.setattr(refresh_module, "PROJECTS",
                            os.path.join(str(tmp_path), "acquisition_projects.json"))
        monkeypatch.setattr(refresh_module, "TARGET_DISTRICTS",
                            os.path.join(str(tmp_path), "target_districts.json"))
        store = RawStore(tmp_path)
        store.put_document(265102, 2025, b"unused: to_text is stubbed below")
        store.record(265102, kept=True)
        store.put_notification(265102, {"doc_id": 265102, "section": "3D",
                                        "state": "WRONG STATE"})
        # Stand in for the PDF text extractor; the point under test is that
        # the stored *document* is re-read, not the stored notification.
        monkeypatch.setattr(refresh_module.documents, "to_text",
                            lambda _: "State: KERALA District: ALAPPUZHA\n"
                                      "New Delhi, the 29th July, 2025\n"
                                      "S.O. 1(E).—conferred by sub-section (1) of section 3A")

        report = refresh_module.refresh(reparse=True, log=lambda *_: None)

        assert report["reparsed"] == 1
        assert store.read_notifications()[0]["state"] == "KERALA"

    def test_reparsing_an_empty_mirror_is_harmless(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "RAW", str(tmp_path))
        monkeypatch.setattr(refresh_module, "PROJECTS",
                            os.path.join(str(tmp_path), "acquisition_projects.json"))
        monkeypatch.setattr(refresh_module, "TARGET_DISTRICTS",
                            os.path.join(str(tmp_path), "target_districts.json"))
        assert refresh_module.refresh(reparse=True, log=lambda *_: None)["reparsed"] == 0


class TestRoundTrip:
    def test_a_stored_notification_survives_json_and_reassembly(self, raw):
        # The mirror stores dates as ISO strings. Everything downstream
        # subtracts them, so they have to come back as dates rather than as
        # strings that merely sort correctly.
        refresh_module.refresh(summarise_only=True, log=lambda *_: None)
        projects = read(os.path.join(raw, "acquisition_projects.json"))
        by_id = {p["project_id"]: p for p in projects}
        # 265102: §3A on 2024-12-23, §3D on 2025-07-29 = 218 days.
        project = by_id["PRJ-ALA-G265102"]
        assert project["notified_3a_on"] == "2024-12-23"
        assert project["declared_3d_on"] == "2025-07-29"
