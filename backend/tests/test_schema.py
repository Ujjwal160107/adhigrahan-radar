from backend.db import get_conn, init_schema

# The original eight linkage-engine tables (PRD 19). s14_load_risk_db must
# never DROP any of these - this is the regression guard for that rule.
LINKAGE_TABLES = {
    "Parcel", "Person", "CourtCase", "CaseParty", "CourtEvent",
    "ParcelCaseLink", "Watchlist", "SourceRecord",
}

# The five risk-engine tables (s8-s15) plus the officer-facing write path
# and the backend-owned audit trail.
RISK_TABLES = {
    "AcquisitionProject", "ProjectStage", "ProjectParcel",
    "ProjectRisk", "ModelRun", "Intervention",
}

AUDIT_TABLES = {"AuditLog"}

ALL_TABLES = LINKAGE_TABLES | RISK_TABLES | AUDIT_TABLES


def _table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows}


def test_schema_creates_exactly_the_contracted_tables(tmp_path):
    conn = get_conn(tmp_path / "t.db")
    init_schema(conn)
    assert _table_names(conn) == ALL_TABLES


def test_original_eight_tables_intact(tmp_path):
    """The 8-table linkage-engine contract (PRD 19) must survive the
    13-table risk-engine extension untouched - schema.sql only ever adds
    tables, never drops or renames one of these eight."""
    conn = get_conn(tmp_path / "t.db")
    init_schema(conn)
    assert LINKAGE_TABLES <= _table_names(conn)


def test_schema_is_idempotent(tmp_path):
    """init_schema must be safe to call twice against the same file - the
    pipeline calls it once per stage (s6, s14) against one shared vivaad.db."""
    conn = get_conn(tmp_path / "t.db")
    init_schema(conn)
    init_schema(conn)  # must not raise
    assert _table_names(conn) == ALL_TABLES
