import csv
import datetime
import io

from flask import Response, flash, redirect, render_template, request, stream_with_context, url_for
from sqlalchemy import func, literal, or_
from sqlalchemy.exc import IntegrityError

from flaskinventory import app, db
from flaskinventory.forms import (
    addlocation,
    addproduct,
    addstaff,
    archiveform,
    deletemovement,
    editlocation,
    editproduct,
    editstaff,
    moveproduct,
)
from flaskinventory.models import Balance, Location, Movement, Product, Staff


DEFAULT_ACTOR = 'Local operator'


PAGE_SIZE = 25


def _page(query, page):
    total = query.order_by(None).count()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    items = query.order_by(None).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE).all()
    return items, total, page, total_pages


def _requested_page():
    try:
        return max(1, int(request.args.get('page', '1')))
    except ValueError:
        return 1


def _stock(product, location_name):
    if location_name == 'Warehouse':
        return product.prod_qty
    location = Location.query.filter_by(loc_name=location_name).first()
    if location is None:
        return 0
    balance = Balance.query.filter_by(
        product_id=product.prod_id, location_id=location.loc_id
    ).first()
    return balance.quantity if balance else 0


def _set_stock(product, location_name, quantity):
    if location_name == 'Warehouse':
        product.prod_qty = quantity
        return
    location = Location.query.filter_by(loc_name=location_name).first()
    if location is None:
        raise ValueError(f"Location '{location_name}' no longer exists.")
    balance = Balance.query.filter_by(
        product_id=product.prod_id, location_id=location.loc_id
    ).first()
    if balance is None:
        balance = Balance(
            product_id=product.prod_id,
            location_id=location.loc_id,
            product=product.prod_name,
            location=location.loc_name,
            quantity=quantity,
        )
        db.session.add(balance)
    else:
        balance.quantity = quantity


def _inventory_entries(product_filter='', location_filter='', page=1):
    warehouse = db.session.query(
        Product.prod_id.label('product_id'),
        Product.prod_name.label('product'),
        literal('Warehouse').label('location'),
        Product.prod_qty.label('quantity'),
        (~Product.active).label('archived'),
    ).filter(Product.prod_qty > 0)
    allocated = db.session.query(
        Product.prod_id.label('product_id'),
        Product.prod_name.label('product'),
        Location.loc_name.label('location'),
        Balance.quantity.label('quantity'),
        or_(~Product.active, ~Location.active).label('archived'),
    ).join(Balance, Balance.product_id == Product.prod_id).join(
        Location, Location.loc_id == Balance.location_id
    ).filter(Balance.quantity > 0)
    inventory = warehouse.union_all(allocated).subquery()
    query = db.session.query(inventory)
    if product_filter:
        query = query.filter(inventory.c.product == product_filter)
    if location_filter:
        query = query.filter(inventory.c.location == location_filter)

    total_entries = query.order_by(None).count()
    total_pages = max(1, (total_entries + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    total_units = query.with_entities(func.coalesce(func.sum(inventory.c.quantity), 0)).scalar()
    product_count = query.with_entities(
        func.count(func.distinct(inventory.c.product_id))
    ).scalar()
    location_count = query.with_entities(
        func.count(func.distinct(inventory.c.location))
    ).scalar()
    rows = query.order_by(inventory.c.product, inventory.c.location).limit(PAGE_SIZE).offset(
        (page - 1) * PAGE_SIZE
    ).all()

    total_stock = Product.prod_qty + func.coalesce(func.sum(Balance.quantity), 0)
    low_stock_rows = db.session.query(
        Product.prod_id,
        Product.prod_name,
        Product.low_stock_threshold,
        total_stock.label('total'),
    ).outerjoin(Balance, Balance.product_id == Product.prod_id).filter(
        Product.active.is_(True),
        Product.low_stock_threshold > 0,
    ).group_by(
        Product.prod_id, Product.prod_name, Product.low_stock_threshold, Product.prod_qty
    ).having(total_stock <= Product.low_stock_threshold).order_by(Product.prod_name).all()
    low_stock_ids = {row.prod_id for row in low_stock_rows}
    entries = [{
        'product': row.product,
        'location': row.location,
        'quantity': row.quantity,
        'archived': row.archived,
        'low_stock': row.product_id in low_stock_ids,
    } for row in rows]
    low_stock = [
        {'product': row.prod_name, 'total': row.total, 'threshold': row.low_stock_threshold}
        for row in low_stock_rows
    ]
    return (
        entries,
        Product.query.order_by(Product.prod_name).all(),
        Location.query.order_by(Location.loc_name).all(),
        low_stock,
        total_entries,
        total_units,
        product_count,
        location_count,
        page,
        total_pages,
    )


@app.route('/Overview')
@app.route('/')
def overview():
    product_filter = request.args.get('product', '').strip()
    location_filter = request.args.get('location', '').strip()
    (
        entries, products, locations, low_stock, total_entries, total_units,
        inventory_products, inventory_locations, page, total_pages,
    ) = _inventory_entries(product_filter, location_filter, _requested_page())
    return render_template(
        'overview.html',
        balance=entries,
        products=products,
        locations=locations,
        selected_product=product_filter,
        selected_location=location_filter,
        low_stock=low_stock,
        total_entries=total_entries,
        total_units=total_units,
        inventory_products=inventory_products,
        inventory_locations=inventory_locations,
        page=page,
        total_pages=total_pages,
    )


@app.route('/Product', methods=['GET', 'POST'])
def product():
    form, eform, archive_form = addproduct(), editproduct(), archiveform()
    if request.method == 'POST':
        product_id = request.form.get('productid')
        if product_id and eform.validate_on_submit():
            item = Product.query.filter_by(prod_id=product_id).first()
            if item is None:
                flash('The product could not be found. Refresh and try again.', 'danger')
                return redirect(url_for('product'))
            item.prod_name = eform.editname.data.strip()
            item.low_stock_threshold = eform.threshold.data
            Balance.query.filter_by(product_id=item.prod_id).update(
                {'product': item.prod_name}, synchronize_session=False
            )
            Movement.query.filter_by(product_id=item.prod_id).update(
                {'pname': item.prod_name}, synchronize_session=False
            )
            return _commit_redirect(url_for('product'), 'Product details updated.')
        if not product_id and form.validate_on_submit():
            item = Product(
                prod_name=form.prodname.data.strip(),
                prod_qty=form.prodqty.data,
                low_stock_threshold=form.threshold.data,
            )
            db.session.add(item)
            return _commit_redirect(url_for('product'), f"Product '{item.prod_name}' added.")
        for errors in (form.errors, eform.errors):
            for field_errors in errors.values():
                for error in field_errors:
                    flash(error, 'danger')

    current_page = _requested_page()
    products, total, current_page, total_pages = _page(
        Product.query.order_by(Product.active.desc(), Product.prod_name), current_page
    )
    return render_template(
        'product.html', title='Products', form=form, eform=eform,
        archive_form=archive_form, details=products,
        total=total, page=current_page, total_pages=total_pages,
    )


@app.route('/Location', methods=['GET', 'POST'])
def loc():
    form, lform, archive_form = addlocation(), editlocation(), archiveform()
    if request.method == 'POST':
        location_id = request.form.get('locid')
        if location_id and lform.validate_on_submit():
            item = Location.query.filter_by(loc_id=location_id).first()
            if item is None:
                flash('The location could not be found. Refresh and try again.', 'danger')
                return redirect(url_for('loc'))
            if lform.editlocname.data.strip().casefold() == 'warehouse':
                flash('The name Warehouse is reserved for the central stock location.', 'danger')
                return redirect(url_for('loc'))
            item.loc_name = lform.editlocname.data.strip()
            Balance.query.filter_by(location_id=item.loc_id).update(
                {'location': item.loc_name}, synchronize_session=False
            )
            Movement.query.filter_by(from_location_id=item.loc_id).update(
                {'frm': item.loc_name}, synchronize_session=False
            )
            Movement.query.filter_by(to_location_id=item.loc_id).update(
                {'to': item.loc_name}, synchronize_session=False
            )
            return _commit_redirect(url_for('loc'), 'Location details updated.')
        if not location_id and form.validate_on_submit():
            if form.locname.data.strip().casefold() == 'warehouse':
                flash('The name Warehouse is reserved for the central stock location.', 'danger')
                return redirect(url_for('loc'))
            item = Location(loc_name=form.locname.data.strip())
            db.session.add(item)
            return _commit_redirect(url_for('loc'), f"Location '{item.loc_name}' added.")
        for errors in (form.errors, lform.errors):
            for field_errors in errors.values():
                for error in field_errors:
                    flash(error, 'danger')

    locations, total, page, total_pages = _page(
        Location.query.order_by(Location.active.desc(), Location.loc_name), _requested_page()
    )
    return render_template(
        'loc.html', title='Locations', form=form, lform=lform,
        archive_form=archive_form, details=locations,
        total=total, page=page, total_pages=total_pages,
    )


def _staff_location_choices(*fields):
    locations = Location.query.order_by(Location.active.desc(), Location.loc_name).all()
    for field in fields:
        field.choices = [(location.loc_id, location.loc_name) for location in locations]


def _assignable_locations(location_ids, current_locations=()):
    """Return the chosen locations, refusing archived ones unless already assigned."""
    current_ids = {location.loc_id for location in current_locations}
    chosen = Location.query.filter(Location.loc_id.in_(location_ids)).all() if location_ids else []
    blocked = [
        location.loc_name for location in chosen
        if not location.active and location.loc_id not in current_ids
    ]
    return chosen, blocked


@app.route('/Staff', methods=['GET', 'POST'])
def staff():
    form, sform, archive_form = addstaff(), editstaff(), archiveform()
    _staff_location_choices(form.locations, sform.editlocations)
    if request.method == 'POST':
        staff_id = request.form.get('staffid')
        if staff_id and sform.validate_on_submit():
            member = Staff.query.filter_by(staff_id=staff_id).first()
            if member is None:
                flash('The staff member could not be found. Refresh and try again.', 'danger')
                return redirect(url_for('staff'))
            chosen, blocked = _assignable_locations(sform.editlocations.data, member.locations)
            if blocked:
                flash('Archived locations cannot get new staff: ' + ', '.join(blocked) + '.', 'danger')
                return redirect(url_for('staff'))
            member.staff_name = sform.editstaffname.data.strip()
            member.role = (sform.editrole.data or '').strip()
            member.locations = chosen
            Movement.query.filter_by(staff_id=member.staff_id).update(
                {'actor': member.staff_name}, synchronize_session=False
            )
            return _commit_redirect(url_for('staff'), 'Staff details updated.')
        if not staff_id and form.validate_on_submit():
            chosen, blocked = _assignable_locations(form.locations.data)
            if blocked:
                flash('Archived locations cannot get new staff: ' + ', '.join(blocked) + '.', 'danger')
                return redirect(url_for('staff'))
            member = Staff(
                staff_name=form.staffname.data.strip(),
                role=(form.role.data or '').strip(),
                locations=chosen,
            )
            db.session.add(member)
            return _commit_redirect(url_for('staff'), f"Staff member '{member.staff_name}' added.")
        for errors in (form.errors, sform.errors):
            for field_errors in errors.values():
                for error in field_errors:
                    flash(error, 'danger')
        return redirect(url_for('staff'))

    members, total, page, total_pages = _page(
        Staff.query.order_by(Staff.active.desc(), Staff.staff_name), _requested_page()
    )
    transfer_counts = dict(
        db.session.query(Movement.staff_id, func.count(Movement.mid))
        .filter(Movement.staff_id.isnot(None))
        .group_by(Movement.staff_id)
        .all()
    )
    return render_template(
        'staff.html', title='Staff', form=form, sform=sform,
        archive_form=archive_form, details=members,
        locations=Location.query.order_by(Location.loc_name).all(),
        transfer_counts=transfer_counts,
        total=total, page=page, total_pages=total_pages,
    )


def _commit_redirect(endpoint, message):
    try:
        db.session.commit()
        flash(message, 'success')
    except IntegrityError:
        db.session.rollback()
        flash('That name is already in use. Choose a unique name and try again.', 'danger')
    except Exception:
        db.session.rollback()
        raise
    return redirect(endpoint)


def _movement_choices(form, movement=None):
    stocked_product_ids = {
        row[0] for row in db.session.query(Balance.product_id).filter(Balance.quantity > 0).distinct()
    }
    stocked_product_ids.update(
        product.prod_id for product in Product.query.filter(Product.prod_qty > 0).all()
    )
    stocked_product_ids.update(
        row[0] for row in db.session.query(Movement.product_id).distinct()
    )
    product_query = Product.query.filter(
        or_(Product.active.is_(True), Product.prod_id.in_(stocked_product_ids))
    ).order_by(Product.prod_name)
    products = product_query.all()
    locations = Location.query.order_by(Location.loc_name).all()
    active_locations = [location for location in locations if location.active]
    source_locations = [location for location in locations if _location_has_stock(location)]
    destination_locations = active_locations
    if movement:
        product = Product.query.filter_by(prod_id=movement.product_id).first()
        if product and product not in products:
            products.append(product)
        source_id = movement.from_location_id
        destination_id = movement.to_location_id
        source_locations.extend(
            location for location in locations
            if location.loc_id == source_id and location not in source_locations
        )
        destination_locations.extend(
            location for location in locations
            if location.loc_id == destination_id and location not in destination_locations
        )
    form.mprodname.choices = [(product.prod_name, product.prod_name) for product in products]
    form.src.choices = [('Warehouse', 'Warehouse')] + [
        (location.loc_name, location.loc_name) for location in source_locations
    ]
    form.destination.choices = [('Warehouse', 'Warehouse')] + [
        (location.loc_name, location.loc_name) for location in destination_locations
    ]
    staff_members = Staff.query.filter_by(active=True).order_by(Staff.staff_name).all()
    staff_choices = list(staff_members)
    if movement and movement.staff_ref and movement.staff_ref not in staff_choices:
        # Keep an archived staff member selectable on the transfer they already handled.
        staff_choices.append(movement.staff_ref)
    form.staff.choices = [('', 'Unassigned')] + [
        (str(member.staff_id), member.staff_name) for member in staff_choices
    ]
    return products, locations, staff_members


def _location_has_stock(location):
    return Balance.query.filter_by(location_id=location.loc_id).filter(Balance.quantity > 0).first() is not None


@app.route('/Transfers', methods=['GET', 'POST'])
def move():
    form = moveproduct()
    movement_id = request.form.get('movement_id') if request.method == 'POST' else None
    movement = Movement.query.filter_by(mid=movement_id).first() if movement_id else None
    products, all_locations, staff_members = _movement_choices(form, movement)

    if request.method == 'POST':
        if movement_id and movement is None:
            flash('The transfer could not be found. Refresh and try again.', 'danger')
            return redirect(url_for('move'))
        if not form.validate_on_submit():
            for field_errors in form.errors.values():
                for error in field_errors:
                    flash(error, 'danger')
            return redirect(url_for('move'))

        product = Product.query.filter_by(prod_name=form.mprodname.data).first()
        if product is None:
            flash('The selected product is no longer available.', 'danger')
            return redirect(url_for('move'))
        handler = None
        if form.staff.data:
            handler = Staff.query.filter_by(staff_id=int(form.staff.data)).first()
            kept_existing = bool(movement and handler and movement.staff_id == handler.staff_id)
            if handler is None or (not handler.active and not kept_existing):
                flash('Archived staff cannot be assigned to transfers. Choose an active staff member.', 'danger')
                return redirect(url_for('move'))
        if movement:
            success, message = _apply_transfer(
                movement.to, movement.frm, movement.pname, movement.pqty
            )
            if not success:
                db.session.rollback()
                flash(f'This transfer cannot be edited safely. {message}', 'danger')
                return redirect(url_for('move'))

        source = form.src.data
        destination = form.destination.data
        if destination != 'Warehouse':
            target = Location.query.filter_by(loc_name=destination).first()
            was_existing_destination = bool(
                target and movement and movement.to == destination and not target.active
            )
            if target is None or (not target.active and not was_existing_destination):
                db.session.rollback()
                flash('Archived locations cannot receive new stock. Choose an active destination.', 'danger')
                return redirect(url_for('move'))

        success, message = _apply_transfer(source, destination, product.prod_name, form.mprodqty.data)
        if not success:
            db.session.rollback()
            flash(message, 'danger')
            return redirect(url_for('move'))

        if movement:
            movement.product_id = product.prod_id
            movement.from_location_id = _location_id(source)
            movement.to_location_id = _location_id(destination)
            movement.frm = source
            movement.to = destination
            movement.pname = product.prod_name
            movement.pqty = form.mprodqty.data
            movement.ts = form.ts.data
            movement.staff_id = handler.staff_id if handler else None
            movement.actor = handler.staff_name if handler else DEFAULT_ACTOR
            movement.updated_at = datetime.datetime.now()
            message = 'Transfer updated and stock balances adjusted.'
        else:
            movement = Movement(
                ts=form.ts.data,
                product_id=product.prod_id,
                from_location_id=_location_id(source),
                to_location_id=_location_id(destination),
                frm=source,
                to=destination,
                pname=product.prod_name,
                pqty=form.mprodqty.data,
                staff_id=handler.staff_id if handler else None,
                actor=handler.staff_name if handler else DEFAULT_ACTOR,
            )
            db.session.add(movement)
            message = 'Transfer recorded successfully.'
        if handler:
            covered = {location.loc_name for location in handler.locations}
            if not covered.intersection({source, destination}):
                flash(
                    f"Note: {handler.staff_name} is not assigned to {source} or {destination}.",
                    'warning',
                )
        return _commit_redirect(url_for('move'), message)

    details, total, page, total_pages = _page(
        Movement.query.order_by(Movement.ts.desc(), Movement.mid.desc()), _requested_page()
    )
    stock_map = {}
    for product in products:
        stock_map[f'{product.prod_name}__Warehouse'] = product.prod_qty
    for balance in Balance.query:
        stock_map[f'{balance.product}__{balance.location}'] = balance.quantity
    return render_template(
        'move.html', title='Transfers', form=form, delete_form=deletemovement(),
        details=details, products=products, locations=all_locations,
        staff_members=staff_members, stock_map=stock_map, total=total, page=page, total_pages=total_pages,
    )


def _location_id(name):
    if name == 'Warehouse':
        return None
    location = Location.query.filter_by(loc_name=name).first()
    return location.loc_id if location else None


def _apply_transfer(source, destination, product_name, quantity):
    if source == destination:
        return False, f"Source and destination cannot be the same ('{source}')."
    product = Product.query.filter_by(prod_name=product_name).first()
    if product is None:
        return False, f"Product '{product_name}' no longer exists."
    available = _stock(product, source)
    if available < quantity:
        return False, (
            f"Not enough stock at '{source}' for '{product_name}'. "
            f"Available: {available}; requested: {quantity}."
        )
    _set_stock(product, source, available - quantity)
    _set_stock(product, destination, _stock(product, destination) + quantity)
    return True, 'Success'


@app.route('/Transfers/<int:movement_id>/delete', methods=['POST'])
def delete_movement(movement_id):
    form = deletemovement()
    if not form.validate_on_submit():
        flash('Could not verify the delete request. Refresh and try again.', 'danger')
        return redirect(url_for('move'))
    movement = Movement.query.filter_by(mid=movement_id).first()
    if movement is None:
        flash('The transfer could not be found. It may already have been deleted.', 'danger')
        return redirect(url_for('move'))
    success, message = _apply_transfer(
        movement.to, movement.frm, movement.pname, movement.pqty
    )
    if not success:
        db.session.rollback()
        flash(f'This transfer cannot be deleted safely. {message}', 'danger')
        return redirect(url_for('move'))
    db.session.delete(movement)
    return _commit_redirect(url_for('move'), 'Transfer deleted and stock balances restored.')


def _filtered_movement_query():
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    product = request.args.get('product', '').strip()
    location = request.args.get('location', '').strip()
    staff_filter = request.args.get('staff', '').strip()
    errors = []
    start, end = None, None
    try:
        if start_date:
            start = datetime.datetime.strptime(start_date, '%Y-%m-%d')
        if end_date:
            end = datetime.datetime.strptime(end_date, '%Y-%m-%d')
    except ValueError:
        errors.append('Enter valid dates using the date picker.')
    if start and end and start > end:
        errors.append('Start date must be on or before end date.')

    query = Movement.query
    if start:
        query = query.filter(Movement.ts >= start)
    if end:
        query = query.filter(Movement.ts < end + datetime.timedelta(days=1))
    if product:
        query = query.filter(Movement.pname == product)
    if location:
        if location == 'Warehouse':
            query = query.filter(or_(
                Movement.from_location_id.is_(None), Movement.to_location_id.is_(None)
            ))
        else:
            loc = Location.query.filter_by(loc_id=location).first()
            if loc is None:
                errors.append('Choose a valid location.')
            else:
                query = query.filter(or_(
                    Movement.from_location_id == loc.loc_id,
                    Movement.to_location_id == loc.loc_id,
                ))
    if staff_filter:
        if staff_filter == 'unassigned':
            query = query.filter(Movement.staff_id.is_(None))
        elif staff_filter.isdigit() and Staff.query.filter_by(staff_id=int(staff_filter)).first():
            query = query.filter(Movement.staff_id == int(staff_filter))
        else:
            errors.append('Choose a valid staff member.')
    return query, errors, start_date, end_date, product, location, staff_filter


@app.route('/Reports')
def reports():
    query, errors, start_date, end_date, product, location, staff_filter = _filtered_movement_query()
    aggregate = (query.with_entities(
        func.count(Movement.mid), func.coalesce(func.sum(Movement.pqty), 0)
    ).first() if not errors else (0, 0))
    details, total, page, total_pages = _page(
        query.order_by(Movement.ts.desc(), Movement.mid.desc()) if not errors else Movement.query.filter(False),
        _requested_page(),
    )
    return render_template(
        'reports.html', title='Reports', details=details,
        products=Product.query.order_by(Product.prod_name).all(),
        locations=Location.query.order_by(Location.loc_name).all(),
        staff_members=Staff.query.order_by(Staff.active.desc(), Staff.staff_name).all(),
        errors=errors, start_date=start_date, end_date=end_date,
        selected_product=product, selected_location=location, selected_staff=staff_filter,
        total_movements=aggregate[0], total_quantity=aggregate[1],
        total=total, page=page, total_pages=total_pages,
    )


def _csv_safe(value):
    value = str(value)
    if value.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


@app.route('/Reports/export')
def export_report():
    query, errors, start_date, end_date, product, location, staff_filter = _filtered_movement_query()
    if errors:
        for error in errors:
            flash(error, 'danger')
        return redirect(url_for(
            'reports', start_date=start_date, end_date=end_date,
            product=product, location=location, staff=staff_filter,
        ))

    def rows():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Movement ID', 'Timestamp', 'From', 'To', 'Product', 'Quantity', 'Handled by'])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for movement in query.order_by(Movement.ts.desc(), Movement.mid.desc()).yield_per(500):
            writer.writerow([
                movement.mid, movement.ts.strftime('%Y-%m-%d %H:%M:%S'),
                _csv_safe(movement.frm), _csv_safe(movement.to),
                _csv_safe(movement.pname), movement.pqty,
                _csv_safe(movement.actor if movement.staff_id else 'Unassigned'),
            ])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return Response(
        stream_with_context(rows()), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=inventory-movement-report.csv'},
    )


ARCHIVABLE = {
    # entity_type: (model, primary key column, page endpoint, message suffix)
    'product': (Product, 'prod_id', 'product', 'without removing its stock or history.'),
    'location': (Location, 'loc_id', 'loc', 'without removing its stock or history.'),
    'staff': (Staff, 'staff_id', 'staff', 'without removing their transfer history.'),
}


@app.route('/Archive/<entity_type>/<int:entity_id>', methods=['POST'])
def archive(entity_type, entity_id):
    if entity_type not in ARCHIVABLE:
        flash('Unknown record type.', 'danger')
        return redirect(url_for('overview'))
    model, key, endpoint, suffix = ARCHIVABLE[entity_type]
    form = archiveform()
    if not form.validate_on_submit():
        flash('Could not verify the archive request. Refresh and try again.', 'danger')
        return redirect(url_for(endpoint))
    action = request.form.get('action')
    if action not in ('archive', 'restore'):
        flash('Choose a valid archive action and try again.', 'danger')
        return redirect(url_for(endpoint))
    item = model.query.filter_by(**{key: entity_id}).first()
    if item is None:
        flash('That record could not be found.', 'danger')
        return redirect(url_for(endpoint))
    archive_action = action == 'archive'
    item.active = not archive_action
    verb = 'archived' if archive_action else 'restored'
    label = 'Staff member' if entity_type == 'staff' else entity_type.title()
    return _commit_redirect(url_for(endpoint), f"{label} {verb} {suffix}")
