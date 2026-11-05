# Inventory-Manager
An inventory management system using Flask

## Getting Started


## Clone this repository and set your path to it's folder, to get it up and running on your local system.

```
git clone https://github.com/marination/Inventory-Manager.git
cd Inventory-Manager
```
## What to look for here?
- [System Summary](#system-summary)
- [Running the app](#running-the-app)
- [Reports](#reports)
- [Backups and local security](#backups-and-local-security)
- Features
  1. [Adding Products and Locations](#adding-products-and-locations)
  2. [Archiving Products and Locations](#archiving-products-and-locations)
  3. [Moving Products](#moving-products)
  4. [Editing Products and Locations](#editing-products-and-locations)
- [Built Using](#built-using)
- [License](#license)
### Prerequisites

To run this system you will need :

- Python 3
- Flask
- SQLALCHEMY
- WTForms

Assuming you have Python, proceed to install the rest using the command below:

```
pip3 install -r requirements.txt
```
## System Summary

This local, single-operator system tracks warehouse and location stock, transfer history, and low-stock thresholds. The Overview, Products, Locations, Transfers, and Reports pages are paginated where appropriate. Product/location archives retain their stock and history; archived stock can be transferred out but cannot receive new stock.

## Reports

Open **Reports** to filter transfer history by inclusive start/end dates, product, and location. The totals cover all matching records, while the table is paginated. **Export CSV** downloads the full matching result set.

## Backups and local security

On the first launch that needs the SQLite schema upgrade, the app creates a timestamped database backup in `instance/` before changing the schema. Keep this backup with the database and create regular backups before making manual database changes. If an upgrade fails validation, startup stops with an error and the original database transaction is rolled back.

The app is intended for one local operator, not public deployment. `run.py` binds the development server to `127.0.0.1`. Set `INVENTORY_SECRET_KEY` to a stable, private random value if you want CSRF sessions to survive application restarts; otherwise, a new secret is generated at startup.

The default SQLite database is `instance/site.db`. Set `INVENTORY_DATABASE_URI` to another SQLAlchemy SQLite URI to use a different local database file. Transfer date/time entries use the local workstation clock; existing history is not rewritten during the schema upgrade.


## Running the app
1) Set your current path to where the cloned folder is and run the file **run.py**

![starting_app](https://user-images.githubusercontent.com/25857446/56443542-c4926380-6312-11e9-98ac-42aa6830bf42.gif)

2) Either copy paste the url as shown above into your browser **or** simply check into *localhost:5000/* as shown below. You will see the initial views of each page as no actions are performed.

![init_site](https://user-images.githubusercontent.com/25857446/56443683-500bf480-6313-11e9-9397-4ec93a34d29d.gif)

## Features

### Adding Products and Locations
Products require product name and quantity to be filled. Location only requires location name


![adding](https://user-images.githubusercontent.com/25857446/56444083-e55bb880-6314-11e9-87de-8deabdc1c6a9.gif)


### Archiving Products and Locations
Archive records instead of deleting them. Existing balances and transfer history remain intact. Restore an archived record when it should accept stock again.


![delete](https://user-images.githubusercontent.com/25857446/56444188-5307e480-6315-11e9-83d8-afaeda5d39ff.gif)


### Moving products
Here products can be moved to a location, from a location as well as to and from a location. Products need to initially be added to various locations from the central warehouse.

Each transfer lets you choose its date and time. You can edit or delete a transfer later; stock balances update with the change, and transfers that can no longer be safely reversed are protected.

![mvng](https://user-images.githubusercontent.com/25857446/56446389-04137c80-6320-11e9-9c68-041db8b00a19.gif)

### Editing Products and Locations
Names can be corrected without losing the associated records. Product minimum stock thresholds can be changed from the product editor. Set starting stock when creating a product, then use transfers to move quantities between locations.


![edit](https://user-images.githubusercontent.com/25857446/56446569-fb6f7600-6320-11e9-85e5-f67e6a454e26.gif)


# Built using
- Flask
- SQLAlchemy

# License

This project is licensed under the MIT License - see the [LICENSE.md](LICENSE.md) file for details
