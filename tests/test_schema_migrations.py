import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from flaskinventory import db
from flaskinventory.migrations import upgrade_inventory_schema


LEGACY_SCHEMA = '''
CREATE TABLE product (
    prod_id INTEGER PRIMARY KEY,
    prod_name VARCHAR(20) UNIQUE NOT NULL,
    prod_qty INTEGER NOT NULL
);
CREATE TABLE location (
    loc_id INTEGER PRIMARY KEY,
    loc_name VARCHAR(20) UNIQUE NOT NULL
);
CREATE TABLE balance (
    bid INTEGER PRIMARY KEY,
    product VARCHAR(20) NOT NULL,
    location VARCHAR(20) NOT NULL,
    quantity INTEGER NOT NULL
);
CREATE TABLE movement (
    mid INTEGER PRIMARY KEY,
    ts DATETIME,
    frm VARCHAR(20) NOT NULL,
    "to" VARCHAR(20) NOT NULL,
    pname VARCHAR(20) NOT NULL,
    pqty INTEGER NOT NULL
);
'''


class SchemaMigrationTests(unittest.TestCase):
    def create_legacy_database(self, directory, include_orphan=False):
        path = Path(directory) / 'legacy.db'
        connection = sqlite3.connect(str(path))
        connection.executescript(LEGACY_SCHEMA)
        connection.execute("INSERT INTO product VALUES (1, 'Widget', 10)")
        connection.execute("INSERT INTO location VALUES (1, 'Shelf')")
        connection.execute("INSERT INTO balance VALUES (1, 'Widget', 'Shelf', 3)")
        if not include_orphan:
            connection.execute("INSERT INTO balance VALUES (2, 'Widget', 'Shelf', 4)")
            connection.execute(
                "INSERT INTO movement VALUES (1, '2026-01-02 10:00', 'Warehouse', 'Shelf', 'Widget', 2)"
            )
        else:
            connection.execute("INSERT INTO balance VALUES (2, 'Missing', 'Shelf', 5)")
        connection.commit()
        connection.close()
        return path

    def test_migration_backs_up_and_merges_duplicate_balances(self):
        with tempfile.TemporaryDirectory(prefix='inventory-schema-test-') as directory:
            path = self.create_legacy_database(directory)
            engine = create_engine('sqlite:///' + str(path).replace('\\', '/'))
            try:
                upgrade_inventory_schema(engine)
                with engine.connect() as connection:
                    balance = connection.execute(text(
                        'SELECT bid, product_id, location_id, quantity FROM balance'
                    )).one()
                    movement = connection.execute(text(
                        'SELECT product_id, from_location_id, to_location_id FROM movement'
                    )).one()
                    self.assertEqual(tuple(balance), (1, 1, 1, 7))
                    self.assertEqual(tuple(movement), (1, None, 1))
                    self.assertEqual(connection.execute(text('PRAGMA foreign_key_check')).all(), [])

                backups = list(Path(directory).glob('legacy.pre-inventory-upgrade-*.db'))
                self.assertEqual(len(backups), 1)
                backup = sqlite3.connect(str(backups[0]))
                self.assertEqual(backup.execute('SELECT COUNT(*) FROM balance').fetchone()[0], 2)
                backup.close()
            finally:
                engine.dispose()

    def test_invalid_legacy_reference_rolls_back_migration(self):
        with tempfile.TemporaryDirectory(prefix='inventory-schema-test-') as directory:
            path = self.create_legacy_database(directory, include_orphan=True)
            engine = create_engine('sqlite:///' + str(path).replace('\\', '/'))
            try:
                with self.assertRaisesRegex(RuntimeError, 'unknown product or location'):
                    upgrade_inventory_schema(engine)
                with engine.connect() as connection:
                    column_names = {
                        column['name'] for column in inspect(connection).get_columns('balance')
                    }
                    self.assertNotIn('product_id', column_names)
                    self.assertEqual(
                        connection.execute(text('SELECT COUNT(*) FROM balance')).scalar(),
                        2,
                    )
                self.assertEqual(len(list(Path(directory).glob('legacy.pre-inventory-upgrade-*.db'))), 1)
            finally:
                engine.dispose()

    def test_empty_database_is_ready_for_create_all(self):
        with tempfile.TemporaryDirectory(prefix='inventory-schema-test-') as directory:
            path = Path(directory) / 'empty.db'
            engine = create_engine('sqlite:///' + str(path).replace('\\', '/'))
            try:
                upgrade_inventory_schema(engine)
                with engine.connect() as connection:
                    self.assertEqual(connection.execute(text(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
                    )).scalar(), 0)
                db.metadata.create_all(engine)
                with engine.connect() as connection:
                    self.assertTrue({
                        'product', 'location', 'balance', 'movement',
                    }.issubset(set(inspect(connection).get_table_names())))
                    self.assertFalse({
                        'stock_adjustment', 'audit_log',
                    }.intersection(set(inspect(connection).get_table_names())))
            finally:
                engine.dispose()


if __name__ == '__main__':
    unittest.main()
