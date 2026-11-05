import os
import secrets
import sqlite3

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, 'connect')
def _enable_sqlite_foreign_keys(connection, _record):
    if isinstance(connection, sqlite3.Connection):
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('INVENTORY_SECRET_KEY') or secrets.token_hex(32)
# Corrected from 'SQLALCHEMY' to 'SQLALCHEMY_DATABASE_URI'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'INVENTORY_DATABASE_URI', 'sqlite:///site.db'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

from flaskinventory import routes
from flaskinventory.migrations import upgrade_inventory_schema

with app.app_context():
    upgrade_inventory_schema(db.engine)
    db.create_all()