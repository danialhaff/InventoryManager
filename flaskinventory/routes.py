import csv
import datetime
import io

from flask import Response, flash, redirect, render_template, request, stream_with_context, url_for
from sqlalchemy import func, literal, or_
from sqlalchemy.exc import IntegrityError

from flaskinventory import app, db
from flaskinventory.forms import (
    addcategory,
    addlocation,
    addproduct,
    archiveform,
    deletemovement,
    editcategory,
    editlocation,
    editproduct,
    moveproduct,
)
from flaskinventory.models import Balance, Category, Location, Movement, Product


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
    choices = _category_choices()
    form.prodcategory.choices = choices
    eform.editprodcategory.choices = choices
    if request.method == 'POST':
        product_id = request.form.get('productid')
        if product_id and eform.validate_on_submit():
            item = Product.query.filter_by(prod_id=product_id).first()
            if item is None:
                flash('The product could not be found. Refresh and try again.', 'danger')
                return redirect(url_for('product'))
            category_id, error = _resolve_category(eform.editprodcategory.data, item.category_id)
            if error:
                flash(error, 'danger')
                return redirect(url_for('product'))
            item.prod_name = eform.editname.data.strip()
            item.low_stock_threshold = eform.threshold.data
            item.category_id = category_id
            Balance.query.filter_by(product_id=item.prod_id).update(
                {'product': item.prod_name}, synchronize_session=False
            )
            Movement.query.filter_by(product_id=item.prod_id).update(
                {'pname': item.prod_name}, synchronize_session=False
            )
            return _commit_redirect(url_for('product'), 'Product details updated.')
        if not product_id and form.validate_on_submit():
            category_id, error = _resolve_category(form.prodcategory.data)
            if error:
                flash(error, 'danger')
                return redirect(url_for('product'))
            item = Product(
                prod_name=form.prodname.data.strip(),
                prod_qty=form.prodqty.data,
                low_stock_threshold=form.threshold.data,
                category_id=category_id,
            )
            db.session.add(item)
            return _commit_redirect(url_for('product'), f"Product '{item.prod_name}' added.")
        for errors in (form.errors, eform.errors):
            for field_errors in errors.values():
                for error in field_errors:
                    flash(error, 'danger')

    selected_category = request.args.get('category', '').strip()
    query = Product.query
    if selected_category == 'none':
        query = query.filter(Product.category_id.is_(None))
    elif selected_category.isdigit():
        query = query.filter(Product.category_id == int(selected_category))

    current_page = _requested_page()
    products, total, current_page, total_pages = _page(
        query.order_by(Product.active.desc(), Product.prod_name), current_page
    )
    return render_template(
        'product.html', title='Products', form=form, eform=eform,
        archive_form=archive_form, details=products,
        categories=Category.query.order_by(Category.cat_name).all(),
        selected_category=selected_category,
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

def _category_choices():
    categories = Category.query.order_by(Category.active.desc(), Category.cat_name).all()
    return [(0, 'No category')] + [
        (c.cat_id, c.cat_name if c.active else f'{c.cat_name} (archived)')
        for c in categories
    ]


def _resolve_category(category_id, current_id=None):
    if not category_id:
        return None, None
    category = Category.query.filter_by(cat_id=category_id).first()
    if category is None:
        return None, 'The selected category no longer exists.'
    if not category.active and category.cat_id != current_id:
        return None, 'Archived categories cannot be assigned to products.'
    return category.cat_id, None


def _category_name_taken(name, exclude_id=None):
    query = Category.query.filter(func.lower(Category.cat_name) == name.lower())
    if exclude_id is not None:
        query = query.filter(Category.cat_id != exclude_id)
    return query.first() is not None


@app.route('/Category', methods=['GET', 'POST'])
def categories():
    form, cform, archive_form = addcategory(), editcategory(), archiveform()
    if request.method == 'POST':
        category_id = request.form.get('catid')
        if category_id and cform.validate_on_submit():
            item = Category.query.filter_by(cat_id=category_id).first()
            if item is None:
                flash('The category could not be found. Refresh and try again.', 'danger')
                return redirect(url_for('categories'))
            name = cform.editcatname.data.strip()
            if _category_name_taken(name, item.cat_id):
                flash('That name is already in use. Choose a unique name and try again.', 'danger')
                return redirect(url_for('categories'))
            item.cat_name = name
            item.description = (cform.editdescription.data or '').strip() or None
            return _commit_redirect(url_for('categories'), 'Category details updated.')
        if not category_id and form.validate_on_submit():
            name = form.catname.data.strip()
            if _category_name_taken(name):
                flash('That name is already in use. Choose a unique name and try again.', 'danger')
                return redirect(url_for('categories'))
            item = Category(
                cat_name=name,
                description=(form.description.data or '').strip() or None,
            )
            db.session.add(item)
            return _commit_redirect(url_for('categories'), f"Category '{item.cat_name}' added.")
        for errors in (form.errors, cform.errors):
            for field_errors in errors.values():
                for error in field_errors:
                    flash(error, 'danger')

    items, total, page, total_pages = _page(
        Category.query.order_by(Category.active.desc(), Category.cat_name), _requested_page()
    )
    counts = dict(
        db.session.query(Product.category_id, func.count(Product.prod_id))
        .filter(Product.category_id.isnot(None))
        .group_by(Product.category_id)
        .all()
    )
    return render_template(
        'category.html', title='Categories', form=form, cform=cform,
        archive_form=archive_form, details=items, counts=counts,
        total=total, page=page, total_pages=total_pages,
    )

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
    return products, locations


def _location_has_stock(location):
    return Balance.query.filter_by(location_id=location.loc_id).filter(Balance.quantity > 0).first() is not None


@app.route('/Transfers', methods=['GET', 'POST'])
def move():
    form = moveproduct()
    movement_id = request.form.get('movement_id') if request.method == 'POST' else None
    movement = Movement.query.filter_by(mid=movement_id).first() if movement_id else None
    products, all_locations = _movement_choices(form, movement)

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
            )
            db.session.add(movement)
            message = 'Transfer recorded successfully.'
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
        stock_map=stock_map, total=total, page=page, total_pages=total_pages,
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
    return query, errors, start_date, end_date, product, location


@app.route('/Reports')
def reports():
    query, errors, start_date, end_date, product, location = _filtered_movement_query()
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
        errors=errors, start_date=start_date, end_date=end_date,
        selected_product=product, selected_location=location,
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
    query, errors, start_date, end_date, product, location = _filtered_movement_query()
    if errors:
        for error in errors:
            flash(error, 'danger')
        return redirect(url_for(
            'reports', start_date=start_date, end_date=end_date,
            product=product, location=location,
        ))

    def rows():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Movement ID', 'Timestamp', 'From', 'To', 'Product', 'Quantity'])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for movement in query.order_by(Movement.ts.desc(), Movement.mid.desc()).yield_per(500):
            writer.writerow([
                movement.mid, movement.ts.strftime('%Y-%m-%d %H:%M:%S'),
                _csv_safe(movement.frm), _csv_safe(movement.to),
                _csv_safe(movement.pname), movement.pqty,
            ])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return Response(
        stream_with_context(rows()), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=inventory-movement-report.csv'},
    )


@app.route('/Archive/<entity_type>/<int:entity_id>', methods=['POST'])
def archive(entity_type, entity_id):
    endpoints = {'product': 'product', 'location': 'loc', 'category': 'categories'}
    endpoint = endpoints.get(entity_type)
    if endpoint is None:
        flash('Unknown record type.', 'danger')
        return redirect(url_for('overview'))
    form = archiveform()
    if not form.validate_on_submit():
        flash('Could not verify the archive request. Refresh and try again.', 'danger')
        return redirect(url_for(endpoint))
    action = request.form.get('action')
    if action not in ('archive', 'restore'):
        flash('Choose a valid archive action and try again.', 'danger')
        return redirect(url_for(endpoint))
    if entity_type == 'product':
        item = Product.query.filter_by(prod_id=entity_id).first()
    elif entity_type == 'location':
        item = Location.query.filter_by(loc_id=entity_id).first()
    else:
        item = Category.query.filter_by(cat_id=entity_id).first()
    if item is None:
        flash('That record could not be found.', 'danger')
        return redirect(url_for(endpoint))
    archive_action = action == 'archive'
    item.active = not archive_action
    verb = 'archived' if archive_action else 'restored'
    if entity_type == 'category':
        message = f'Category {verb}. Products keep their category assignment.'
    else:
        message = f'{entity_type.title()} {verb} without removing its stock or history.'
    return _commit_redirect(url_for(endpoint), message)
