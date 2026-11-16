import os
import sqlite3
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

TEST_DATABASE_DIRECTORY = tempfile.TemporaryDirectory(prefix='inventory-test-')
TEST_DATABASE_PATH = Path(TEST_DATABASE_DIRECTORY.name) / 'site.db'
SOURCE_DATABASE_PATH = Path(__file__).resolve().parents[1] / 'instance' / 'site.db'
source_database = sqlite3.connect(str(SOURCE_DATABASE_PATH))
test_database = sqlite3.connect(str(TEST_DATABASE_PATH))
source_database.backup(test_database)
test_database.close()
source_database.close()
os.environ['INVENTORY_DATABASE_URI'] = (
    'sqlite:///' + str(TEST_DATABASE_PATH).replace('\\', '/')
)

from flaskinventory import app, db
from flaskinventory.models import Balance, Location, Movement, Product, Staff
from sqlalchemy import text


class CSRFTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tokens = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'input' and attributes.get('name') == 'csrf_token':
            token = attributes.get('value')
            if token:
                self.tokens.append(token)


class InventoryWorkflowTests(unittest.TestCase):
    def setUp(self):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        source = sqlite3.connect(str(SOURCE_DATABASE_PATH))
        destination = sqlite3.connect(str(TEST_DATABASE_PATH))
        source.backup(destination)
        destination.close()
        source.close()
        with app.app_context():
            db.create_all()
        self.client = app.test_client()

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        try:
            TEST_DATABASE_DIRECTORY.cleanup()
        except PermissionError:
            pass
        os.environ.pop('INVENTORY_DATABASE_URI', None)

    def csrf_token(self, response):
        parser = CSRFTokenParser()
        parser.feed(response.get_data(as_text=True))
        self.assertTrue(parser.tokens, 'Expected a CSRF token in the form.')
        return parser.tokens[0]

    def create_product_and_location(self):
        response = self.client.get('/Product')
        self.client.post('/Product', data={
            'csrf_token': self.csrf_token(response),
            'prodname': 'TestWidget',
            'prodqty': '10',
            'threshold': '3',
            'prodsubmit': 'Save product',
        })
        response = self.client.get('/Location')
        self.client.post('/Location', data={
            'csrf_token': self.csrf_token(response),
            'locname': 'TestShelf',
            'locsubmit': 'Save location',
        })
        with app.app_context():
            product = Product.query.filter_by(prod_name='TestWidget').one()
            location = Location.query.filter_by(loc_name='TestShelf').one()
            return product.prod_id, location.loc_id

    def test_inventory_changes_preserve_balances_and_transfer_history(self):
        with app.app_context():
            initial_counts = (Balance.query.count(), Movement.query.count())
            self.assertEqual(
                db.session.execute(text('PRAGMA foreign_keys')).scalar(),
                1,
            )
        self.assertEqual(self.client.get('/').status_code, 200)
        with app.app_context():
            self.assertEqual(initial_counts, (Balance.query.count(), Movement.query.count()))

        product_id, location_id = self.create_product_and_location()
        with app.app_context():
            product = Product.query.filter_by(prod_id=product_id).one()
            self.assertEqual((product.prod_qty, product.low_stock_threshold), (10, 3))

        transfer = {
            'mprodname': 'TestWidget',
            'src': 'Warehouse',
            'destination': 'TestShelf',
            'mprodqty': '4',
            'ts': '2026-01-02T12:30',
            'movesubmit': 'Confirm transfer',
        }
        response = self.client.get('/Transfers')
        transfer['csrf_token'] = self.csrf_token(response)
        self.client.post('/Transfers', data=transfer)
        with app.app_context():
            movement = Movement.query.filter_by(product_id=product_id).one()
            movement_id = movement.mid
            self.assertEqual(movement.ts.strftime('%Y-%m-%d %H:%M'), '2026-01-02 12:30')
            self.assertEqual(Product.query.filter_by(prod_id=product_id).one().prod_qty, 6)
            self.assertEqual(
                Balance.query.filter_by(product_id=product_id, location_id=location_id).one().quantity,
                4,
            )

        response = self.client.get('/Transfers')
        self.client.post('/Transfers', data={
            **transfer,
            'csrf_token': self.csrf_token(response),
            'movement_id': str(movement_id),
            'mprodqty': '3',
            'movesubmit': 'Update transfer',
        })
        with app.app_context():
            self.assertEqual(Movement.query.filter_by(mid=movement_id).one().pqty, 3)
            self.assertEqual(Product.query.filter_by(prod_id=product_id).one().prod_qty, 7)

        report = self.client.get(
            '/Reports?start_date=2026-01-02&end_date=2026-01-02&product=TestWidget'
        )
        self.assertIn('<strong>1</strong> movements', report.get_data(as_text=True))
        exported = self.client.get(
            '/Reports/export?start_date=2026-01-02&end_date=2026-01-02&product=TestWidget'
        )
        self.assertIn(b'TestWidget', exported.get_data())

        response = self.client.get('/Transfers')
        self.client.post(
            f'/Transfers/{movement_id}/delete',
            data={'csrf_token': self.csrf_token(response), 'submit': 'Delete'},
        )
        with app.app_context():
            self.assertIsNone(Movement.query.filter_by(mid=movement_id).first())
            self.assertEqual(Product.query.filter_by(prod_id=product_id).one().prod_qty, 10)
            self.assertEqual(
                Balance.query.filter_by(product_id=product_id, location_id=location_id).one().quantity,
                0,
            )

        response = self.client.get('/Product')
        self.client.post(f'/Archive/product/{product_id}', data={
            'csrf_token': self.csrf_token(response),
            'action': 'archive',
            'submit': 'Confirm',
        })
        with app.app_context():
            self.assertFalse(Product.query.filter_by(prod_id=product_id).one().active)
        self.assertIn(b'Archived', self.client.get('/Overview?product=TestWidget').data)

        response = self.client.get('/Location')
        self.client.post(f'/Archive/location/{location_id}', data={
            'csrf_token': self.csrf_token(response),
            'action': 'archive',
            'submit': 'Confirm',
        })
        with app.app_context():
            self.assertFalse(Location.query.filter_by(loc_id=location_id).one().active)

        response = self.client.get('/Location')
        self.client.post('/Location', data={
            'csrf_token': self.csrf_token(response),
            'locname': 'ActiveShelf',
            'locsubmit': 'Save location',
        })
        with app.app_context():
            active_location_id = Location.query.filter_by(loc_name='ActiveShelf').one().loc_id

        response = self.client.get('/Transfers')
        self.client.post('/Transfers', data={
            'csrf_token': self.csrf_token(response),
            'mprodname': 'TestWidget',
            'src': 'Warehouse',
            'destination': 'ActiveShelf',
            'mprodqty': '2',
            'ts': '2026-01-02T13:00',
            'movesubmit': 'Confirm transfer',
        })
        with app.app_context():
            self.assertEqual(
                Balance.query.filter_by(product_id=product_id, location_id=active_location_id).one().quantity,
                2,
            )

        response = self.client.get('/Location')
        self.client.post(f'/Archive/location/{active_location_id}', data={
            'csrf_token': self.csrf_token(response),
            'action': 'archive',
            'submit': 'Confirm',
        })
        response = self.client.get('/Transfers')
        self.client.post('/Transfers', data={
            'csrf_token': self.csrf_token(response),
            'mprodname': 'TestWidget',
            'src': 'Warehouse',
            'destination': 'ActiveShelf',
            'mprodqty': '1',
            'ts': '2026-01-02T13:15',
            'movesubmit': 'Confirm transfer',
        })
        with app.app_context():
            self.assertEqual(
                Balance.query.filter_by(product_id=product_id, location_id=active_location_id).one().quantity,
                2,
            )
        response = self.client.get('/Transfers')
        self.client.post('/Transfers', data={
            'csrf_token': self.csrf_token(response),
            'mprodname': 'TestWidget',
            'src': 'ActiveShelf',
            'destination': 'Warehouse',
            'mprodqty': '2',
            'ts': '2026-01-02T13:30',
            'movesubmit': 'Confirm transfer',
        })
        with app.app_context():
            self.assertEqual(
                Balance.query.filter_by(product_id=product_id, location_id=active_location_id).one().quantity,
                0,
            )
            self.assertFalse(Location.query.filter_by(loc_id=active_location_id).one().active)
        self.assertEqual(self.client.get('/Adjustments').status_code, 404)
        self.assertEqual(self.client.get('/Audit').status_code, 404)

    def test_staff_assignment_to_locations_and_transfers(self):
        product_id, location_id = self.create_product_and_location()

        response = self.client.get('/Staff')
        self.assertEqual(response.status_code, 200)
        self.client.post('/Staff', data={
            'csrf_token': self.csrf_token(response),
            'staffname': 'TestAisyah',
            'role': 'Store keeper',
            'locations': [str(location_id)],
            'staffsubmit': 'Save staff',
        })
        with app.app_context():
            member = Staff.query.filter_by(staff_name='TestAisyah').one()
            staff_id = member.staff_id
            self.assertEqual(member.role, 'Store keeper')
            self.assertEqual([loc.loc_id for loc in member.locations], [location_id])
        self.assertIn(b'TestAisyah', self.client.get('/Location').data)

        response = self.client.get('/Transfers')
        self.assertIn(b'TestAisyah', response.data)
        self.client.post('/Transfers', data={
            'csrf_token': self.csrf_token(response),
            'mprodname': 'TestWidget',
            'src': 'Warehouse',
            'destination': 'TestShelf',
            'mprodqty': '2',
            'staff': str(staff_id),
            'ts': '2026-02-03T09:00',
            'movesubmit': 'Confirm transfer',
        })
        with app.app_context():
            movement = Movement.query.filter_by(product_id=product_id).one()
            movement_id = movement.mid
            self.assertEqual((movement.staff_id, movement.actor), (staff_id, 'TestAisyah'))

        report = self.client.get(f'/Reports?product=TestWidget&staff={staff_id}')
        self.assertIn('<strong>1</strong> movements', report.get_data(as_text=True))
        report = self.client.get('/Reports?product=TestWidget&staff=unassigned')
        self.assertIn('<strong>0</strong> movements', report.get_data(as_text=True))
        exported = self.client.get(f'/Reports/export?product=TestWidget&staff={staff_id}')
        self.assertIn(b'Handled by', exported.data)
        self.assertIn(b'TestAisyah', exported.data)

        # Renaming keeps the transfer linked and updates its recorded name.
        response = self.client.get('/Staff')
        self.client.post('/Staff', data={
            'csrf_token': self.csrf_token(response),
            'staffid': str(staff_id),
            'editstaffname': 'TestAisyahR',
            'editrole': '',
            'editstaffsubmit': 'Save changes',
        })
        with app.app_context():
            member = Staff.query.filter_by(staff_id=staff_id).one()
            self.assertEqual((member.staff_name, member.role, member.locations), ('TestAisyahR', '', []))
            self.assertEqual(Movement.query.filter_by(mid=movement_id).one().actor, 'TestAisyahR')

        # Archived staff keep history but cannot take new transfers.
        response = self.client.get('/Staff')
        self.client.post(f'/Archive/staff/{staff_id}', data={
            'csrf_token': self.csrf_token(response),
            'action': 'archive',
            'submit': 'Confirm',
        })
        with app.app_context():
            self.assertFalse(Staff.query.filter_by(staff_id=staff_id).one().active)
        response = self.client.get('/Transfers')
        self.client.post('/Transfers', data={
            'csrf_token': self.csrf_token(response),
            'mprodname': 'TestWidget',
            'src': 'Warehouse',
            'destination': 'TestShelf',
            'mprodqty': '1',
            'staff': str(staff_id),
            'ts': '2026-02-03T10:00',
            'movesubmit': 'Confirm transfer',
        })
        with app.app_context():
            self.assertEqual(Movement.query.filter_by(product_id=product_id).count(), 1)
            self.assertEqual(Product.query.filter_by(prod_id=product_id).one().prod_qty, 8)

        # Editing the old transfer can keep its archived handler.
        response = self.client.get('/Transfers')
        self.client.post('/Transfers', data={
            'csrf_token': self.csrf_token(response),
            'movement_id': str(movement_id),
            'mprodname': 'TestWidget',
            'src': 'Warehouse',
            'destination': 'TestShelf',
            'mprodqty': '3',
            'staff': str(staff_id),
            'ts': '2026-02-03T09:00',
            'movesubmit': 'Update transfer',
        })
        with app.app_context():
            movement = Movement.query.filter_by(mid=movement_id).one()
            self.assertEqual((movement.pqty, movement.staff_id), (3, staff_id))

    def test_archive_get_request_cannot_delete_records(self):
        product_id, _location_id = self.create_product_and_location()
        response = self.client.get(f'/delete?type=product&p_id={product_id}')
        self.assertEqual(response.status_code, 404)
        with app.app_context():
            self.assertIsNotNone(Product.query.filter_by(prod_id=product_id).first())


if __name__ == '__main__':
    unittest.main()
