from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import os
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)

# SQLite database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///pinks.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

# Models
class PinkSlip(db.Model):
    __tablename__ = 'pink_slip'

    id = db.Column(db.Integer, primary_key=True)
    slip_number = db.Column(db.String(100), unique=True, nullable=False)
    customer_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(50))
    date_received = db.Column(db.String(30))
    due_date = db.Column(db.String(30))
    total_amount = db.Column(db.Float, default=0.0, nullable=False)

    items = db.relationship(
        'PinkSlipItem',
        backref='slip',
        cascade='all, delete-orphan',
        lazy='select'
    )

    def __repr__(self):
        return f"<PinkSlip {self.slip_number} - {self.customer_name}>"

class PinkSlipItem(db.Model):
    __tablename__ = 'pink_slip_item'

    id = db.Column(db.Integer, primary_key=True)
    slip_id = db.Column(db.Integer, db.ForeignKey('pink_slip.id'), nullable=False)
    item_type = db.Column(db.String(100), nullable=False)
    other_item_desc = db.Column(db.String(500))
    price = db.Column(db.Float, nullable=False)

    def __repr__(self):
        return f"<PinkSlipItem {self.id} - {self.item_type} - ${self.price}>"

def _format_date_val(val):
    if pd.isna(val) or val == '':
        return ''
    if isinstance(val, pd.Timestamp):
        # include time only if present
        if val.time().hour == 0 and val.time().minute == 0 and val.time().second == 0:
            return val.strftime('%Y-%m-%d')
        return val.strftime('%Y-%m-%d %H:%M')
    # fallback: try to parse string-ish values
    try:
        parsed = pd.to_datetime(val)
        if parsed.time().hour == 0 and parsed.time().minute == 0 and parsed.time().second == 0:
            return parsed.strftime('%Y-%m-%d')
        return parsed.strftime('%Y-%m-%d %H:%M')
    except Exception:
        return str(val)[:19]  # short fallback

@app.route("/")
def home():
    return """
    <h1>Pink Slip Management System</h1>
    <form method="POST" action="/upload" enctype="multipart/form-data">
        <input type="file" name="file">
        <input type="submit" value="Upload CSV/Excel">
    </form>
    """

@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get('file')
    if not file:
        return "No file uploaded", 400

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(filepath)

    if file.filename.endswith('.csv'):
        df = pd.read_csv(filepath, dtype=str).fillna('')
    elif file.filename.endswith(('.xls', '.xlsx')):
        df = pd.read_excel(filepath, dtype=str).fillna('')
    else:
        return "Unsupported file type", 400

    # counters
    tickets_created = 0
    items_imported = 0
    duplicates_skipped = 0
    rows_skipped = 0

    # cache tickets processed in this upload to avoid repeated DB queries
    tickets_cache = {}

    for _, row in df.iterrows():
        slip_number = str(row.get('slip_number', '')).strip()
        if not slip_number:
            rows_skipped += 1
            continue

        # normalize dates
        date_received = _format_date_val(row.get('date_received', ''))
        due_date = _format_date_val(row.get('due_date', ''))

        customer_name = str(row.get('customer_name', '')).strip()
        phone = str(row.get('phone', '')).strip()
        item_type = str(row.get('item_type', '')).strip()
        other_item_desc = str(row.get('other_item_desc', '')).strip()

        # price must be float and required for item
        price_raw = row.get('price', '')
        try:
            price = float(price_raw) if price_raw != '' else 0.0
        except Exception:
            price = 0.0

        # get or create ticket (cache per slip_number)
        ticket = tickets_cache.get(slip_number)
        if ticket is None:
            ticket = PinkSlip.query.filter_by(slip_number=slip_number).first()
            if ticket is None:
                ticket = PinkSlip(
                    slip_number=slip_number,
                    customer_name=customer_name or 'Unknown',
                    phone=phone,
                    date_received=date_received,
                    due_date=due_date,
                    total_amount=0.0
                )
                db.session.add(ticket)
                tickets_created += 1
            else:
                # update contact/dates only if they are empty on the ticket and provided in CSV
                if customer_name and not ticket.customer_name:
                    ticket.customer_name = customer_name
                if phone and not ticket.phone:
                    ticket.phone = phone
                if date_received and not ticket.date_received:
                    ticket.date_received = date_received
                if due_date and not ticket.due_date:
                    ticket.due_date = due_date
            tickets_cache[slip_number] = ticket

        # Optional duplicate item check per ticket:
        duplicate_found = False
        for existing_item in ticket.items:
            if (existing_item.item_type == item_type and
                (existing_item.other_item_desc or '') == (other_item_desc or '') and
                float(existing_item.price) == float(price)):
                duplicate_found = True
                break

        if duplicate_found:
            duplicates_skipped += 1
            continue

        # create item and attach to ticket
        item = PinkSlipItem(
            slip=ticket,
            item_type=item_type or 'Other',
            other_item_desc=other_item_desc,
            price=price
        )
        db.session.add(item)
        items_imported += 1

    # After adding all items, update total_amount for each ticket
    # Note: ticket.items will reflect newly added items because of relationship
    for ticket in tickets_cache.values():
        # compute sum of prices for items currently associated with the ticket
        total = 0.0
        for it in ticket.items:
            try:
                total += float(it.price)
            except Exception:
                continue
        ticket.total_amount = total
        db.session.add(ticket)

    # Commit once
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Database integrity error during import. No changes were committed.", 500

    return (
        f"Import complete: {tickets_created} tickets created, "
        f"{items_imported} items imported, {duplicates_skipped} duplicate items skipped, "
        f"{rows_skipped} rows skipped (missing slip_number)."
    )

@app.route("/records")
def records():
    tickets = PinkSlip.query.order_by(PinkSlip.slip_number).all()
    if not tickets:
        return "No records found."

    html = "<h1>All Tickets</h1>"
    for t in tickets:
        html += (
            f"<h2>Ticket: {t.slip_number} | Customer: {t.customer_name} | Phone: {t.phone}</h2>"
            f"<p>Date Received: {t.date_received or 'N/A'} | Due: {t.due_date or 'N/A'} | Total: ${t.total_amount:.2f}</p>"
        )
        html += "<ul>"
        for it in t.items:
            other = f" - {it.other_item_desc}" if it.other_item_desc else ""
            html += f"<li>{it.item_type}{other} - ${it.price:.2f}</li>"
        html += "</ul>"

    return html

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
