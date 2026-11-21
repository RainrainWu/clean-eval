"""SQLite: a database opened read-only (code-review-graph's graph.db). A failure to open or query raises HeatmapError."""
import sqlite3

from ..domain.errors import HeatmapError


class SqliteGateway:
    def __init__(self, path):
        self.path = path
        try:
            self._con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        except sqlite3.Error as e:
            raise HeatmapError("cannot open the database %s: %s" % (path, e))

    def rows(self, query, params=()):
        """Every row `query` returns, as tuples."""
        try:
            return self._con.execute(query, params).fetchall()
        except sqlite3.Error as e:
            raise HeatmapError("cannot read the database %s: %s" % (self.path, e))

    def close(self):
        self._con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
