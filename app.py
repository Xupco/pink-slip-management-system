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
    first_initial = db.Column(db.String(5), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    date_received = db.Column(db.String(30), nullable=False)
    due_date = db.Column(db.String(30), nullable=False)
    due_time = db.Column(db.String(20), nullable=True)
    total_amount = db.Column(db.Float, default=0.0, nullable=False)

    items = db.relationship(
        'PinkSlipItem',
        backref='slip',
        cascade='all, delete-orphan',
        lazy='select'
    )

    def __repr__(self):
        return f"<PinkSlip {self.slip_number} - {self.first_initial}. {self.last_name}>"

class PinkSlipItem(db.Model):
    __tablename__ = 'pink_slip_item'

    id = db.Column(db.Integer, primary_key=True)
    slip_id = db.Column(db.Integer, db.ForeignKey('pink_slip.id'), nullable=False)
    item_type = db.Column(db.String(100), nullable=False)
    other_item_desc = db.Column(db.String(500))
    price = db.Column(db.Float, nullable=False)

    def __repr__(self):
        return f"<PinkSlipItem {self.id} - {self.item_type} - ${self.price}>"

# date cleaner -> returns date in MM/DD/YYYY format
def _format_date_val(val):
    # return empty if values are missing
    if pd.isna(val) or val == '':
        return ''
    # check if already parsed as Timestamp
    if isinstance(val, pd.Timestamp):
        return val.strftime('%m/%d/%Y')
    # fallback for string values
    try:
        parsed = pd.to_datetime(val, format='%m/%d/%Y')
        return parsed.strftime('%m/%d/%Y')
    except Exception:
        # try flexible parsing as fallback
        try:
            parsed = pd.to_datetime(val)
            return parsed.strftime('%m/%d/%Y')
        except Exception:
            return str(val)[:10]

# time extractor -> returns time in 12 hour format
def _format_time_val(val):
    # return empty if values are missing
    if pd.isna(val) or val == '':
        return ''
    if isinstance(val, pd.Timestamp):
        # no time component if midnight
        if val.time().hour == 0 and val.time().minute == 0 and val.time().second == 0:
            return ''
        return val.strftime('%I:%M %p').lstrip('0')
    # fallback for string values
    try:
        parsed = pd.to_datetime(val)
        if parsed.time().hour == 0 and parsed.time().minute == 0 and parsed.time().second == 0:
            return ''
        return parsed.strftime('%I:%M %p').lstrip('0')
    except Exception:
        return ''

# format phone number to (XXX) XXX-XXXX, defaulting to 704 area code if given 7 digit phone number
def _format_phone(phone_str):
    if not phone_str or pd.isna(phone_str):
        return ''
    # strip all non digit characters
    digits = ''.join(c for c in str(phone_str) if c.isdigit())
    if not digits:
        return ''
    # if 7 digits, prepend 704 area code
    if len(digits) == 7:
        digits = '704' + digits
    # if 10 digits, format as (XXX) XXX-XXXX
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    # if 11 digits starting with 1, strip the 1 and format
    if len(digits) == 11 and digits[0] == '1':
        digits = digits[1:]
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    # return original if cant parse
    return str(phone_str).strip()

# convert any time string to 12 hour format (normalizes to XX:XX AM or XX:XX PM)
def _convert_to_12hr(time_str):
    if not time_str or pd.isna(time_str):
        return ''
    time_str = str(time_str).strip()
    if not time_str:
        return ''
    # try parsing and normalizing (handles 2:30pm, 2:30 pm, 2:30Pm, 14:30, etc.)
    try:
        parsed = pd.to_datetime(time_str)
        return parsed.strftime('%I:%M %p').lstrip('0')
    except Exception:
        return time_str

# valid item types for pink slips
VALID_ITEM_TYPES = ['Shirt', 'Jeans', 'Dress', 'Jacket', 'Coat', 'Pants', 'Skirt', 'Shorts', 'Other']

# common variations and misspellings mapped to valid types
ITEM_TYPE_ALIASES = {
    # Shirt variations
    'shirts': 'Shirt', 'tshirt': 'Shirt', 't-shirt': 'Shirt', 'tee': 'Shirt', 'blouse': 'Shirt', 'top': 'Shirt',
    # Jeans variations
    'jean': 'Jeans', 'denim': 'Jeans',
    # Dress variations
    'dresses': 'Dress', 'gown': 'Dress',
    # Jacket variations
    'jackets': 'Jacket', 'blazer': 'Jacket',
    # Coat variations
    'coats': 'Coat', 'overcoat': 'Coat',
    # Pants variations
    'pant': 'Pants', 'trousers': 'Pants', 'slacks': 'Pants',
    # Skirt variations
    'skirts': 'Skirt',
    # Shorts variations
    'short': 'Shorts',
    # Other variations
    'misc': 'Other', 'miscellaneous': 'Other', 'etc': 'Other',
}

def _normalize_item_type(item_type_str):
    # normalize item type to valid category which returns (normalized_type, is_valid)
    if not item_type_str or pd.isna(item_type_str):
        return None, False

    item_type_str = str(item_type_str).strip()
    if not item_type_str:
        return None, False

    # check exact match (case-insensitive)
    for valid_type in VALID_ITEM_TYPES:
        if item_type_str.lower() == valid_type.lower():
            return valid_type, True

    # check aliases
    lower_input = item_type_str.lower()
    if lower_input in ITEM_TYPE_ALIASES:
        return ITEM_TYPE_ALIASES[lower_input], True

    # check if input starts with or contains a valid type
    for valid_type in VALID_ITEM_TYPES:
        if lower_input.startswith(valid_type.lower()) or valid_type.lower() in lower_input:
            return valid_type, True

    # unrecognized -> return None to indicate invalid
    return None, False

@app.route("/")
def home():
    return """
    <h1>Pink Slip Management System</h1>
    <form method="POST" action="/upload" enctype="multipart/form-data">
        <input type="file" name="file">
        <input type="submit" value="Upload CSV/Excel">
    </form>
    <div style="margin-bottom: 20px;">
        <a href="/add_pink_slip"><button type="button">Add A Pink Slip</button></a>
    </div>
    <div>
        <a href="/records"><button type="button">View All Records</button></a>
    </div>
    """

@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get('file')
    if not file:
        return """
        <h1>Error: No File Uploaded</h1>
        <p>Please select a file before uploading.</p>
        <a href="/"><button type="button">Back to Upload</button></a>
        """, 400

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

    # validation errors (per user instructions)
    error_rows = []  # list of dicts: {row, slip_number, error}

    # cache tickets processed in this upload to avoid repeated DB queries
    tickets_cache = {}

    # iterate with excel style row numbers: idx is dataframe index (0-based for first data row)
    for idx, row in df.iterrows():
        row_number = idx + 2  # +2 accounts for header row and 0-index

        # read raw values first (do NOT normalize/convert dates yet)
        slip_number = str(row.get('slip_number', '')).strip()
        first_initial = str(row.get('first_initial', '')).strip().upper()[:1]
        last_name = str(row.get('last_name', '')).strip()
        phone = _format_phone(row.get('phone', ''))
        item_type_raw = str(row.get('item_type', '')).strip()
        other_item_desc = str(row.get('other_item_desc', '')).strip()
        price_raw = str(row.get('price', '')).strip()
        date_received_raw = row.get('date_received', '')
        due_date_raw = row.get('due_date', '')
        due_time_raw = row.get('due_time', '')

        # validation block: must occur before any ticket lookup, duplicate detection, or item creation

        # slip_number required
        if not slip_number:
            rows_skipped += 1
            error_rows.append({
                "row": row_number,
                "slip_number": "",
                "error": "Missing slip_number"
            })
            continue

        # item_type required and must be valid
        item_type, item_type_valid = _normalize_item_type(item_type_raw)
        if not item_type_valid:
            rows_skipped += 1
            error_rows.append({
                "row": row_number,
                "slip_number": slip_number,
                "error": f"Invalid item_type: '{item_type_raw}'. Valid types: {', '.join(VALID_ITEM_TYPES)}"
            })
            continue

        # price must parse and be non-negative
        try:
            price = float(price_raw)
        except Exception:
            rows_skipped += 1
            error_rows.append({
                "row": row_number,
                "slip_number": slip_number,
                "error": "Invalid price format"
            })
            continue

        if price < 0:
            rows_skipped += 1
            error_rows.append({
                "row": row_number,
                "slip_number": slip_number,
                "error": "Negative price not allowed"
            })
            continue

        # all validations passed for this row now normalize dates and proceed
        date_received = _format_date_val(date_received_raw)
        due_date = _format_date_val(due_date_raw)
        # use explicit due_time column if provided, otherwise extract from due_date
        if due_time_raw and str(due_time_raw).strip():
            due_time = _convert_to_12hr(due_time_raw)
        else:
            due_time = _format_time_val(due_date_raw)

        # get or create ticket (cache per slip_number)
        ticket = tickets_cache.get(slip_number)
        if ticket is None:
            ticket = PinkSlip.query.filter_by(slip_number=slip_number).first()
            if ticket is None:
                ticket = PinkSlip(
                    slip_number=slip_number,
                    first_initial=first_initial or '?',
                    last_name=last_name or 'Unknown',
                    phone=phone,
                    date_received=date_received,
                    due_date=due_date,
                    due_time=due_time,
                    total_amount=0.0
                )
                db.session.add(ticket)
                tickets_created += 1
            else:
                # update contact/dates only if they are empty on the ticket and provided in CSV
                if first_initial and not ticket.first_initial:
                    ticket.first_initial = first_initial
                if last_name and not ticket.last_name:
                    ticket.last_name = last_name
                if phone and not ticket.phone:
                    ticket.phone = phone
                if date_received and not ticket.date_received:
                    ticket.date_received = date_received
                if due_date and not ticket.due_date:
                    ticket.due_date = due_date
                if due_time and not ticket.due_time:
                    ticket.due_time = due_time
            tickets_cache[slip_number] = ticket

        # optional duplicate item check per ticket:
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

        # create item and attach to ticket using validated item_type and price (no fallback)
        item = PinkSlipItem(
            slip=ticket,
            item_type=item_type,
            other_item_desc=other_item_desc,
            price=price
        )
        db.session.add(item)
        items_imported += 1

    # after adding all items, update total_amount for each ticket
    for ticket in tickets_cache.values():
        total = 0.0
        for it in ticket.items:
            try:
                total += float(it.price)
            except Exception:
                continue
        ticket.total_amount = total
        db.session.add(ticket)

    # commit once
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Database integrity error during import. No changes were committed.", 500

    # build HTML response with import summary and rejected rows table
    html = "<h1>Upload Results</h1>"
    html += '<a href="/"><button type="button">Upload Another File</button></a> '
    html += '<a href="/records"><button type="button">View All Records</button></a>'
    html += (
        f"<p><b>Import complete:</b> {tickets_created} tickets created, "
        f"{items_imported} items imported, {duplicates_skipped} duplicate items skipped, "
        f"{rows_skipped} rows rejected.</p>"
    )

    if error_rows:
        html += "<h3>Rejected Rows</h3>"
        html += "<table border='1' cellpadding='5'>"
        html += "<tr><th>Row</th><th>Slip Number</th><th>Error</th></tr>"
        for err in error_rows:
            html += (
                f"<tr>"
                f"<td>{err['row']}</td>"
                f"<td>{err['slip_number']}</td>"
                f"<td>{err['error']}</td>"
                f"</tr>"
            )
        html += "</table>"

    return html

@app.route("/records")
def records():
    # get search query from URL parameters
    search_query = request.args.get('search', '').strip()

    # start with all tickets
    query = PinkSlip.query

    # apply search filter if provided
    if search_query:
        search_filter = (
            PinkSlip.slip_number.ilike(f'%{search_query}%') |
            PinkSlip.first_initial.ilike(f'%{search_query}%') |
            PinkSlip.last_name.ilike(f'%{search_query}%') |
            PinkSlip.phone.ilike(f'%{search_query}%')
        )
        query = query.filter(search_filter)

    tickets = query.order_by(PinkSlip.slip_number).all()

    html = "<h1>All Tickets</h1>"
    html += '<a href="/"><button type="button">Back to Upload</button></a><br><br>'

    # add search form
    html += '''
    <form method="GET" action="/records">
        <input type="text" name="search" placeholder="Search by slip number, customer, or phone"
               value="{}" size="40">
        <input type="submit" value="Search">
        <a href="/records"><button type="button">Clear Search</button></a>
    </form>
    <br>
    '''.format(search_query)

    if search_query:
        html += f"<p><i>Showing results for: '{search_query}' ({len(tickets)} ticket(s) found)</i></p>"

    if not tickets:
        return html + "<p>No records found.</p>"

    for t in tickets:
        html += (
            f"<h2>Ticket: {t.slip_number} | Customer: {t.first_initial}. {t.last_name} | Phone: {t.phone}</h2>"
            f"<p>Date Received: {t.date_received or 'N/A'} | Due: {t.due_date or 'N/A'}"
            f"{(' at ' + t.due_time) if t.due_time else ''} | Total: ${t.total_amount:.2f}</p>"
        )
        html += "<ul>"
        for it in t.items:
            other = f" - {it.other_item_desc}" if it.other_item_desc else ""
            html += f"<li>{it.item_type}{other} - ${it.price:.2f}</li>"
        html += "</ul>"

    return html

@app.route("/add_pink_slip", methods=["GET", "POST"])
def add_pink_slip():
    if request.method == "POST":
        # Collect form data
        slip_number = request.form.get("slip_number", "").strip()
        first_initial = request.form.get("first_initial", "").strip().upper()[:1]
        last_name = request.form.get("last_name", "").strip()
        phone = _format_phone(request.form.get("phone", ""))
        date_received = _format_date_val(request.form.get("date_received", ""))
        due_date = _format_date_val(request.form.get("due_date", ""))
        due_time = _convert_to_12hr(request.form.get("due_time", ""))
        item_type_raw = request.form.get("item_type", "").strip()
        other_item_desc = request.form.get("other_item_desc", "").strip()
        price_raw = request.form.get("price", "").strip()

        # Validate item type
        item_type, item_type_valid = _normalize_item_type(item_type_raw)
        if not item_type_valid:
            return f"Invalid item type. Valid options: {', '.join(VALID_ITEM_TYPES)}", 400

        # Validate price
        try:
            price = float(price_raw)
            if price < 0:
                raise ValueError
        except ValueError:
            return "Invalid price. Must be a positive number.", 400

        # Create ticket if it doesn't exist
        ticket = PinkSlip.query.filter_by(slip_number=slip_number).first()
        if not ticket:
            ticket = PinkSlip(
                slip_number=slip_number,
                first_initial=first_initial or '?',
                last_name=last_name or 'Unknown',
                phone=phone,
                date_received=date_received,
                due_date=due_date,
                due_time=due_time,
                total_amount=0.0
            )
            db.session.add(ticket)

        # Add item
        item = PinkSlipItem(
            slip=ticket,
            item_type=item_type,
            other_item_desc=other_item_desc,
            price=price
        )
        db.session.add(item)

        # Update total amount
        ticket.total_amount = sum(it.price for it in ticket.items)
        db.session.commit()

        return f"Pink slip {slip_number} added successfully! <a href='/records'>View Records</a>"

    # GET method: show form
    return """
    <h1>Add Pink Slip</h1>
    <form method="POST">
        Slip Number: <input type="text" name="slip_number" required><br>
        First Initial: <input type="text" name="first_initial" maxlength="1"><br>
        Last Name: <input type="text" name="last_name"><br>
        Phone: <input type="text" name="phone"><br>
        Date Received (MM/DD/YYYY): <input type="text" name="date_received"><br>
        Due Date (MM/DD/YYYY): <input type="text" name="due_date"><br>
        Due Time (HH:MM AM/PM): <input type="text" name="due_time"><br>
        Item Type: <input type="text" name="item_type" required><br>
        Other Item Description: <input type="text" name="other_item_desc"><br>
        Price: <input type="text" name="price" required><br>
        <input type="submit" value="Add Pink Slip">
    </form>
    <br>
    <a href="/"><button type="button">Back to Home</button></a>
    """

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
