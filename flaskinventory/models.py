from flaskinventory import db
from datetime import datetime
from sqlalchemy import UniqueConstraint

class Location(db.Model):
    loc_id = db.Column(db.Integer, primary_key= True)
    loc_name = db.Column(db.String(20),unique = True, nullable = False)
    active = db.Column(db.Boolean, nullable=False, default=True, server_default='1')

    def __repr__(self):
        return f"Location('{self.loc_id}','{self.loc_name}')"

class Product(db.Model):
    prod_id = db.Column(db.Integer, primary_key= True)
    prod_name = db.Column(db.String(20),unique = True ,nullable = False)
    prod_qty = db.Column(db.Integer, nullable = False)
    low_stock_threshold = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    active = db.Column(db.Boolean, nullable=False, default=True, server_default='1')
    __table_args__ = (
        db.CheckConstraint('prod_qty >= 0', name='ck_product_nonnegative_quantity'),
        db.CheckConstraint('low_stock_threshold >= 0', name='ck_product_nonnegative_threshold'),
    )

    def __repr__(self):
        return f"Product('{self.prod_id}','{self.prod_name}','{self.prod_qty}')"

class Movement(db.Model):
    mid = db.Column(db.Integer, primary_key= True)
    ts = db.Column(db.DateTime, default=datetime.now)
    product_id = db.Column(db.Integer, db.ForeignKey('product.prod_id', onupdate='CASCADE'), nullable=False, index=True)
    from_location_id = db.Column(db.Integer, db.ForeignKey('location.loc_id', onupdate='CASCADE'), index=True)
    to_location_id = db.Column(db.Integer, db.ForeignKey('location.loc_id', onupdate='CASCADE'), index=True)
    frm = db.Column(db.String(20), nullable = False)
    to = db.Column(db.String(20), nullable = False)
    pname = db.Column(db.String(20), nullable = False)
    pqty = db.Column(db.Integer, nullable = False)
    actor = db.Column(db.String(80), nullable=False, default='Local operator', server_default='Local operator')
    updated_at = db.Column(db.DateTime)
    __table_args__ = (
        db.CheckConstraint('pqty > 0', name='ck_movement_positive_quantity'),
    )

    product_ref = db.relationship('Product', foreign_keys=[product_id])
    from_location_ref = db.relationship('Location', foreign_keys=[from_location_id])
    to_location_ref = db.relationship('Location', foreign_keys=[to_location_id])

    def __repr__(self):
        return f"Movement('{self.mid}','{self.ts}','{self.frm}','{self.to}','{self.pname}','{self.pqty}')"

class Balance(db.Model):
    bid = db.Column(db.Integer, primary_key= True,nullable = False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.prod_id', onupdate='CASCADE'), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('location.loc_id', onupdate='CASCADE'), nullable=False)
    product = db.Column(db.String(20), nullable = False)
    location = db.Column(db.String(20),nullable = False)
    quantity = db.Column(db.Integer, nullable = False)

    __table_args__ = (
        UniqueConstraint('product_id', 'location_id', name='uq_balance_product_location'),
        db.CheckConstraint('quantity >= 0', name='ck_balance_nonnegative_quantity'),
    )

    product_ref = db.relationship('Product', foreign_keys=[product_id])
    location_ref = db.relationship('Location', foreign_keys=[location_id])

    def __repr__(self):
        return f"Balance('{self.bid}','{self.product}','{self.location}','{self.quantity}')"
