import datetime
import sqlite3
from pathlib import Path

from sqlalchemy import inspect, text


def _backup_database(connection):
    database_path = connection.execute(text('PRAGMA database_list')).fetchone()[2]
    if not database_path:
        return

    source_path = Path(database_path)
    if not source_path.exists():
        return

    backup_path = source_path.with_name(
        f'{source_path.stem}.pre-inventory-upgrade-{datetime.datetime.now():%Y%m%d%H%M%S%f}{source_path.suffix}'
    )
    with sqlite3.connect(str(source_path)) as source, sqlite3.connect(str(backup_path)) as backup:
        source.backup(backup)


def upgrade_inventory_schema(engine):
    if engine.dialect.name != 'sqlite':
        raise RuntimeError('The inventory schema upgrade currently supports SQLite only.')

    inspector = inspect(engine)
    required_tables = {'product', 'location', 'balance', 'movement'}
    present_tables = set(inspector.get_table_names())
    inventory_tables = present_tables.intersection(required_tables)
    if not inventory_tables:
        return
    missing_tables = required_tables - present_tables
    if missing_tables:
        raise RuntimeError(
            'Cannot upgrade inventory: the database has an incomplete inventory schema '
            f'(missing {", ".join(sorted(missing_tables))}).'
        )

    additions = {
        'product': {
            'low_stock_threshold': "INTEGER NOT NULL DEFAULT 0 CHECK (low_stock_threshold >= 0)",
            'active': "BOOLEAN NOT NULL DEFAULT 1",
            'category_id': 'INTEGER REFERENCES category(cat_id) ON UPDATE CASCADE',
        },
        'location': {
            'active': "BOOLEAN NOT NULL DEFAULT 1",
        },
        'balance': {
            'product_id': 'INTEGER REFERENCES product(prod_id) ON UPDATE CASCADE',
            'location_id': 'INTEGER REFERENCES location(loc_id) ON UPDATE CASCADE',
        },
        'movement': {
            'product_id': 'INTEGER REFERENCES product(prod_id) ON UPDATE CASCADE',
            'from_location_id': 'INTEGER REFERENCES location(loc_id) ON UPDATE CASCADE',
            'to_location_id': 'INTEGER REFERENCES location(loc_id) ON UPDATE CASCADE',
            "actor": "VARCHAR(80) NOT NULL DEFAULT 'Local operator'",
            'updated_at': 'DATETIME',
        },
    }

    pending = {}
    for table, columns in additions.items():
        existing = {column['name'] for column in inspector.get_columns(table)}
        missing = {name: definition for name, definition in columns.items() if name not in existing}
        if missing:
            pending[table] = missing

    with engine.begin() as connection:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        duplicate_balances = connection.execute(text(
            'SELECT 1 FROM balance GROUP BY product, location HAVING COUNT(*) > 1 LIMIT 1'
        )).fetchone()
        expected_objects = {
            'uq_balance_product_location',
            'ix_movement_ts_mid',
            'ix_movement_product_id',
            'ix_movement_location_date',
            'trg_balance_quantity_nonnegative_insert',
            'trg_balance_quantity_nonnegative_update',
            'trg_balance_identity_required_insert',
            'trg_balance_identity_required_update',
            'trg_product_inventory_nonnegative_insert',
            'trg_product_inventory_nonnegative_update',
            'trg_movement_quantity_positive_insert',
            'trg_movement_quantity_positive_update',
            'trg_movement_product_required_insert',
            'trg_movement_product_required_update',
        }
        existing_objects = set(connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')"
        )).scalars())
        if pending or duplicate_balances or expected_objects - existing_objects:
            _backup_database(connection)
            for table, columns in pending.items():
                for name, definition in columns.items():
                    connection.execute(text(
                        f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}'
                    ))
                    
            connection.execute(text(
            'CREATE TABLE IF NOT EXISTS category ('
            'cat_id INTEGER NOT NULL PRIMARY KEY, '
            'cat_name VARCHAR(30) NOT NULL UNIQUE, '
            'description VARCHAR(100), '
            'active BOOLEAN NOT NULL DEFAULT 1)'
        ))

        reserved_location = connection.execute(text(
            "SELECT loc_id FROM location WHERE lower(loc_name) = 'warehouse' LIMIT 1"
        )).fetchone()
        if reserved_location:
            raise RuntimeError(
                f'Cannot upgrade inventory: location {reserved_location[0]} uses the reserved name Warehouse. '
                'Rename it before restarting the app.'
            )

        connection.execute(text(
            'UPDATE balance SET product_id = '
            '(SELECT prod_id FROM product WHERE product.prod_name = balance.product) '
            'WHERE product_id IS NULL'
        ))
        connection.execute(text(
            'UPDATE balance SET location_id = '
            '(SELECT loc_id FROM location WHERE location.loc_name = balance.location) '
            'WHERE location_id IS NULL'
        ))
        connection.execute(text(
            'UPDATE movement SET product_id = '
            '(SELECT prod_id FROM product WHERE product.prod_name = movement.pname) '
            'WHERE product_id IS NULL'
        ))
        connection.execute(text(
            "UPDATE movement SET from_location_id = NULL WHERE frm = 'Warehouse' AND from_location_id IS NULL"
        ))
        connection.execute(text(
            "UPDATE movement SET from_location_id = "
            '(SELECT loc_id FROM location WHERE location.loc_name = movement.frm) '
            "WHERE frm <> 'Warehouse' AND from_location_id IS NULL"
        ))
        connection.execute(text(
            "UPDATE movement SET to_location_id = NULL WHERE \"to\" = 'Warehouse' AND to_location_id IS NULL"
        ))
        connection.execute(text(
            'UPDATE movement SET to_location_id = '
            '(SELECT loc_id FROM location WHERE location.loc_name = movement."to") '
            'WHERE movement."to" <> \'Warehouse\' AND to_location_id IS NULL'
        ))

        invalid_balances = connection.execute(text(
            'SELECT bid FROM balance WHERE product_id IS NULL OR location_id IS NULL LIMIT 1'
        )).fetchone()
        if invalid_balances:
            raise RuntimeError(
                f'Cannot upgrade inventory: balance {invalid_balances[0]} references an unknown product or location.'
            )

        invalid_movements = connection.execute(text(
            'SELECT mid FROM movement WHERE product_id IS NULL '
            'OR (frm <> \'Warehouse\' AND from_location_id IS NULL) '
            'OR ("to" <> \'Warehouse\' AND to_location_id IS NULL) LIMIT 1'
        )).fetchone()
        if invalid_movements:
            raise RuntimeError(
                f'Cannot upgrade inventory: transfer {invalid_movements[0]} references an unknown product or location.'
            )

        invalid_stock = connection.execute(text(
            'SELECT prod_id FROM product WHERE prod_qty < 0 OR low_stock_threshold < 0 LIMIT 1'
        )).fetchone()
        if invalid_stock:
            raise RuntimeError(
                f'Cannot upgrade inventory: product {invalid_stock[0]} has a negative quantity or threshold.'
            )
        invalid_stock = connection.execute(text(
            'SELECT bid FROM balance WHERE quantity < 0 LIMIT 1'
        )).fetchone()
        if invalid_stock:
            raise RuntimeError(
                f'Cannot upgrade inventory: balance {invalid_stock[0]} has a negative quantity.'
            )
        invalid_movement = connection.execute(text(
            'SELECT mid FROM movement WHERE pqty <= 0 LIMIT 1'
        )).fetchone()
        if invalid_movement:
            raise RuntimeError(
                f'Cannot upgrade inventory: transfer {invalid_movement[0]} has an invalid quantity.'
            )

        duplicates = connection.execute(text(
            'SELECT product_id, location_id, MIN(bid), SUM(quantity) '
            'FROM balance GROUP BY product_id, location_id HAVING COUNT(*) > 1'
        )).fetchall()
        for product_id, location_id, keep_id, total in duplicates:
            connection.execute(text(
                'UPDATE balance SET quantity = :quantity WHERE bid = :bid'
            ), {'quantity': total, 'bid': keep_id})
            connection.execute(text(
                'DELETE FROM balance WHERE product_id = :product_id AND location_id = :location_id AND bid <> :keep_id'
            ), {'product_id': product_id, 'location_id': location_id, 'keep_id': keep_id})

        connection.execute(text(
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_balance_product_location '
            'ON balance (product_id, location_id)'
        ))
        connection.execute(text(
            'CREATE INDEX IF NOT EXISTS ix_movement_ts_mid ON movement (ts, mid)'
        ))
        connection.execute(text(
            'CREATE INDEX IF NOT EXISTS ix_movement_product_id ON movement (product_id)'
        ))
        connection.execute(text(
            'CREATE INDEX IF NOT EXISTS ix_movement_location_date '
            'ON movement (from_location_id, to_location_id, ts, mid)'
        ))
        connection.execute(text(
            'CREATE TRIGGER IF NOT EXISTS trg_balance_quantity_nonnegative_insert '
            'BEFORE INSERT ON balance WHEN NEW.quantity < 0 '
            "BEGIN SELECT RAISE(ABORT, 'balance quantity cannot be negative'); END"
        ))
        connection.execute(text(
            'CREATE TRIGGER IF NOT EXISTS trg_balance_identity_required_insert '
            'BEFORE INSERT ON balance WHEN NEW.product_id IS NULL OR NEW.location_id IS NULL '
            "BEGIN SELECT RAISE(ABORT, 'balance product and location are required'); END"
        ))
        connection.execute(text(
            'CREATE TRIGGER IF NOT EXISTS trg_balance_quantity_nonnegative_update '
            'BEFORE UPDATE OF quantity ON balance WHEN NEW.quantity < 0 '
            "BEGIN SELECT RAISE(ABORT, 'balance quantity cannot be negative'); END"
        ))
        connection.execute(text(
            'CREATE TRIGGER IF NOT EXISTS trg_balance_identity_required_update '
            'BEFORE UPDATE OF product_id, location_id ON balance '
            'WHEN NEW.product_id IS NULL OR NEW.location_id IS NULL '
            "BEGIN SELECT RAISE(ABORT, 'balance product and location are required'); END"
        ))
        connection.execute(text(
            'CREATE TRIGGER IF NOT EXISTS trg_product_inventory_nonnegative_insert '
            'BEFORE INSERT ON product WHEN NEW.prod_qty < 0 OR NEW.low_stock_threshold < 0 '
            "BEGIN SELECT RAISE(ABORT, 'product quantities cannot be negative'); END"
        ))
        connection.execute(text(
            'CREATE TRIGGER IF NOT EXISTS trg_product_inventory_nonnegative_update '
            'BEFORE UPDATE OF prod_qty, low_stock_threshold ON product '
            'WHEN NEW.prod_qty < 0 OR NEW.low_stock_threshold < 0 '
            "BEGIN SELECT RAISE(ABORT, 'product quantities cannot be negative'); END"
        ))
        connection.execute(text(
            'CREATE TRIGGER IF NOT EXISTS trg_movement_quantity_positive_insert '
            'BEFORE INSERT ON movement WHEN NEW.pqty <= 0 '
            "BEGIN SELECT RAISE(ABORT, 'movement quantity must be positive'); END"
        ))
        connection.execute(text(
            'CREATE TRIGGER IF NOT EXISTS trg_movement_product_required_insert '
            'BEFORE INSERT ON movement WHEN NEW.product_id IS NULL '
            "BEGIN SELECT RAISE(ABORT, 'movement product is required'); END"
        ))
        connection.execute(text(
            'CREATE TRIGGER IF NOT EXISTS trg_movement_quantity_positive_update '
            'BEFORE UPDATE OF pqty ON movement WHEN NEW.pqty <= 0 '
            "BEGIN SELECT RAISE(ABORT, 'movement quantity must be positive'); END"
        ))
        connection.execute(text(
            'CREATE TRIGGER IF NOT EXISTS trg_movement_product_required_update '
            'BEFORE UPDATE OF product_id ON movement WHEN NEW.product_id IS NULL '
            "BEGIN SELECT RAISE(ABORT, 'movement product is required'); END"
        ))

        violations = connection.execute(text('PRAGMA foreign_key_check')).fetchall()
        if violations:
            raise RuntimeError(
                f'Cannot upgrade inventory: SQLite reported a foreign-key violation: {violations[0]}.'
            )
