"""The on-disk mirror of everything harvested from the network.

`data/raw/` is the boundary between the network and the build. The ingest
layer only ever writes here; the offline build only ever reads here. That is
what makes a rebuild deterministic and lets the demo run with no government
server reachable.

The store also remembers what it has already *looked at*, including documents
it decided not to keep, so a refresh never re-downloads a notification it
already rejected and an interrupted harvest resumes instead of restarting.
Government hosts are slow enough that this is a practical requirement, not a
nicety.

Deliberately not a database. The unit of storage is a file named after the
document it came from, so the mirror can be inspected, diffed and pruned with
ordinary tools, and "what do we have?" is answerable without running code.
"""
import json
import os


class RawStore:
    """Documents, parsed notifications, and the record of what has been seen."""

    def __init__(self, root):
        self._root = str(root)
        self._base = os.path.join(self._root, "egazette")
        self._index_path = os.path.join(self._base, "index.json")
        self._notifications = os.path.join(self._base, "notifications")
        # Source PDFs are megabytes each and stay out of git; the parsed
        # notifications beside them are kilobytes and are committed, so the
        # build needs no network. One prefix keeps that split to one
        # .gitignore line.
        self._pdf = os.path.join(self._base, "pdf")
        self._index = self._load_index()

    # ---- source documents --------------------------------------------------

    def put_document(self, doc_id, year, content):
        """Mirror one gazette PDF, partitioned by publication year exactly as
        the source partitions it."""
        path = self._document_path(doc_id, year)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(content)

    def read_document(self, doc_id):
        path = self._find_document(doc_id)
        if path is None:
            return None
        with open(path, "rb") as fh:
            return fh.read()

    def holds(self, doc_id):
        """Whether the source document itself is on disk."""
        return self._find_document(doc_id) is not None

    # ---- parsed notifications ----------------------------------------------

    def put_notification(self, doc_id, record):
        os.makedirs(self._notifications, exist_ok=True)
        with open(self._notification_path(doc_id), "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=1, default=str)

    def read_notifications(self):
        """Every parsed notification, in document-id order.

        Stable ordering is what lets the build downstream be deterministic:
        `os.listdir` is not sorted, so leaving it unsorted would make the
        composed corpus depend on filesystem iteration order.
        """
        if not os.path.isdir(self._notifications):
            return []
        out = []
        for name in sorted(os.listdir(self._notifications), key=_document_id_of):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(self._notifications, name), encoding="utf-8") as fh:
                out.append(json.load(fh))
        return out

    # ---- what has already been considered ----------------------------------

    def record(self, doc_id, kept, reason=None):
        """Note that this document has been handled, and why.

        `reason` is what makes a small harvest auditable: a yield that drops
        is explainable from the index rather than being a mystery.
        """
        self._index[str(doc_id)] = {"kept": bool(kept), "reason": reason}
        self._write_index()

    def has_seen(self, doc_id):
        return str(doc_id) in self._index

    def kept(self, doc_id):
        return bool(self._index.get(str(doc_id), {}).get("kept"))

    # ---- internals ---------------------------------------------------------

    def _document_path(self, doc_id, year):
        return os.path.join(self._pdf, str(year), f"{doc_id}.pdf")

    def _find_document(self, doc_id):
        """Locate a document without being told its year.

        Callers reading the mirror back (the build, a re-parse) have a
        document id and nothing else; requiring them to carry the year too
        would push the storage layout into every caller.
        """
        if not os.path.isdir(self._pdf):
            return None
        for year in sorted(os.listdir(self._pdf)):
            path = os.path.join(self._pdf, year, f"{doc_id}.pdf")
            if os.path.exists(path):
                return path
        return None

    def _notification_path(self, doc_id):
        return os.path.join(self._notifications, f"{doc_id}.json")

    def _load_index(self):
        if not os.path.exists(self._index_path):
            return {}
        with open(self._index_path, encoding="utf-8") as fh:
            return json.load(fh)

    def _write_index(self):
        os.makedirs(self._base, exist_ok=True)
        with open(self._index_path, "w", encoding="utf-8") as fh:
            json.dump(self._index, fh, indent=1, sort_keys=True)


def _document_id_of(filename):
    """Sort key: numeric where the name is a document id, so 265102 comes
    before 265154 rather than after it as a string comparison would have it
    for ids of differing length."""
    stem = filename.rsplit(".", 1)[0]
    return (0, int(stem)) if stem.isdigit() else (1, 0)
