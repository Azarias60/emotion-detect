"""Connection and transaction policy shared by the support repositories."""

import sqlite3
from contextlib import closing, contextmanager


@contextmanager
def open_database(path):
    with closing(sqlite3.connect(path, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            yield connection
