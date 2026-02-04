from flask import Flask, request, Response
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import os
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///pinks.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

class PinkSlip(db.Model):
    __tablename__ = 'pink_slip'

    id = db.Column(db.Integer, primary_key=True)
    slip_number = db.Column(db.String(6), unique=True, nullable=False)
    first_initial = db.Column(db.String(1), nullable=False)
    last_name = db.Column(db.String(30), nullable=False)
    phone = db.Column(db.String(16), nullable=False)
    date_received = db.Column(db.String(10), nullable=False)
    due_date = db.Column(db.String(10), nullable=False) # generally 14 day turn around
    due_time = db.Column(db.String(8), nullable=True) # due times usually range between 10am-6pm
    total_amount = db.Column(db.Float, default=0.0, nullable=False)

    items = db.relationship(
        'PinkSlipItem',
        backref='slip',
        cascade='all, delete-orphan',
        lazy='select'
    )

class PinkSlipItem(db.Model):
    __tablename__ = 'pink_slip_item'

    id = db.Column(db.Integer, primary_key=True)
    slip_id = db.Column(db.Integer, db.ForeignKey('pink_slip.id'), nullable=False)
    item_type = db.Column(db.String(10), nullable=False)
    work_description = db.Column(db.String(100)) # if the item type is 'Other', the work description should include both the actual item and the work to be done
    price = db.Column(db.Float, nullable=False)

def _format_date_val(val):
    if pd.isna(val) or val == '':
        return ''
    if isinstance(val, pd.Timestamp):
        return val.strftime('%m/%d/%Y')
    try:
        parsed = pd.to_datetime(val, format='%m/%d/%Y')
        return parsed.strftime('%m/%d/%Y')
    except Exception:
        try:
            parsed = pd.to_datetime(val)
            return parsed.strftime('%m/%d/%Y')
        except Exception:
            return str(val)[:10]

def _parse_time(val):
    if not val or pd.isna(val) or str(val).strip() == '':
        return ''
    if isinstance(val, pd.Timestamp):
        if val.time().hour == 0 and val.time().minute == 0:
            return ''
        return val.strftime('%I:%M %p').lstrip('0')
    try:
        parsed = pd.to_datetime(str(val).strip())
        if parsed.time().hour == 0 and parsed.time().minute == 0:
            return ''
        return parsed.strftime('%I:%M %p').lstrip('0')
    except Exception:
        return ''

def _format_phone(phone_str):
    if not phone_str or pd.isna(phone_str):
        return ''
    digits = ''.join(c for c in str(phone_str) if c.isdigit())
    if not digits:
        return ''
    if len(digits) == 7:  # default to 704 area code if given 7 digit number considering some old-school Charlotte natives prefer this way 
        digits = '704' + digits
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == '1':
        digits = digits[1:]
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return str(phone_str).strip()

VALID_ITEM_TYPES = ['Shirt', 'Jeans', 'Dress', 'Jacket', 'Coat', 'Pants', 'Skirt', 'Shorts', 'Other']

ITEM_TYPE_ALIASES = {
    'shirts': 'Shirt', 'tshirt': 'Shirt', 't-shirt': 'Shirt', 'tee': 'Shirt', 'blouse': 'Shirt', 'top': 'Shirt',
    'jean': 'Jeans', 'denim': 'Jeans',
    'dresses': 'Dress', 'gown': 'Dress',
    'jackets': 'Jacket', 'blazer': 'Jacket',
    'coats': 'Coat', 'overcoat': 'Coat',
    'pant': 'Pants', 'trousers': 'Pants', 'slacks': 'Pants',
    'skirts': 'Skirt',
    'short': 'Shorts',
    'misc': 'Other', 'miscellaneous': 'Other', 'etc': 'Other',
}

def _normalize_item_type(item_type_str):
    if not item_type_str or pd.isna(item_type_str):
        return None, False

    item_type_str = str(item_type_str).strip()
    if not item_type_str:
        return None, False

    for valid_type in VALID_ITEM_TYPES:
        if item_type_str.lower() == valid_type.lower():
            return valid_type, True

    lower_input = item_type_str.lower()
    if lower_input in ITEM_TYPE_ALIASES:
        return ITEM_TYPE_ALIASES[lower_input], True

    for valid_type in VALID_ITEM_TYPES:
        if lower_input.startswith(valid_type.lower()) or valid_type.lower() in lower_input:
            return valid_type, True

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
    <div style="margin-bottom: 20px;">
        <a href="/records"><button type="button">View All Records</button></a>
    </div>
    <div>
        <a href="/export"><button type="button">Export CSV</button></a>
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

    slips_created = 0
    items_imported = 0
    duplicates_skipped = 0
    rows_skipped = 0
    error_rows = []
    slips_cache = {}

    for idx, row in df.iterrows():
        row_number = idx + 2  # account for header row

        slip_number = str(row.get('slip_number', '')).strip()
        first_initial = str(row.get('first_initial', '')).strip().upper()[:1]
        last_name = str(row.get('last_name', '')).strip()
        phone = _format_phone(row.get('phone', ''))
        item_type_raw = str(row.get('item_type', '')).strip()
        work_description = str(row.get('work_description', '')).strip()
        price_raw = str(row.get('price', '')).strip().replace('$', '').replace(',', '')
        date_received_raw = row.get('date_received', '')
        due_date_raw = row.get('due_date', '')
        due_time_raw = row.get('due_time', '')

        if not slip_number:
            rows_skipped += 1
            error_rows.append({
                "row": row_number,
                "slip_number": "",
                "error": "Missing slip_number"
            })
            continue

        item_type, item_type_valid = _normalize_item_type(item_type_raw)
        if not item_type_valid:
            rows_skipped += 1
            error_rows.append({
                "row": row_number,
                "slip_number": slip_number,
                "error": f"Invalid item_type: '{item_type_raw}'. Valid types: {', '.join(VALID_ITEM_TYPES)}"
            })
            continue

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

        date_received = _format_date_val(date_received_raw)
        due_date = _format_date_val(due_date_raw)
        if due_time_raw and str(due_time_raw).strip():
            due_time = _parse_time(due_time_raw)
        else:
            due_time = _parse_time(due_date_raw)

        pink_slip = slips_cache.get(slip_number)
        if pink_slip is None:
            pink_slip = PinkSlip.query.filter_by(slip_number=slip_number).first()
            if pink_slip is None:
                pink_slip = PinkSlip(
                    slip_number=slip_number,
                    first_initial=first_initial or '?',
                    last_name=last_name or 'Unknown',
                    phone=phone,
                    date_received=date_received,
                    due_date=due_date,
                    due_time=due_time,
                    total_amount=0.0
                )
                db.session.add(pink_slip)
                slips_created += 1
            else:
                # fill in any missing fields from the CSV
                if first_initial and not pink_slip.first_initial:
                    pink_slip.first_initial = first_initial
                if last_name and not pink_slip.last_name:
                    pink_slip.last_name = last_name
                if phone and not pink_slip.phone:
                    pink_slip.phone = phone
                if date_received and not pink_slip.date_received:
                    pink_slip.date_received = date_received
                if due_date and not pink_slip.due_date:
                    pink_slip.due_date = due_date
                if due_time and not pink_slip.due_time:
                    pink_slip.due_time = due_time
            slips_cache[slip_number] = pink_slip

        # skip duplicate items on the same slip
        duplicate_found = False
        for existing_item in pink_slip.items:
            if (existing_item.item_type == item_type and
                (existing_item.work_description or '') == (work_description or '') and
                float(existing_item.price) == float(price)):
                duplicate_found = True
                break

        if duplicate_found:
            duplicates_skipped += 1
            continue

        item = PinkSlipItem(
            slip=pink_slip,
            item_type=item_type,
            work_description=work_description,
            price=price
        )
        db.session.add(item)
        items_imported += 1

    # recalculate totals
    for pink_slip in slips_cache.values():
        total = 0.0
        for it in pink_slip.items:
            try:
                total += float(it.price)
            except Exception:
                continue
        pink_slip.total_amount = total
        db.session.add(pink_slip)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Database integrity error during import. No changes were committed.", 500

    html = "<h1>Upload Results</h1>"
    html += '<a href="/"><button type="button">Upload Another File</button></a> '
    html += '<a href="/records"><button type="button">View All Records</button></a>'
    html += (
        f"<p><b>Import complete:</b> {slips_created} slips created, "
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
    search_query = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 25

    query = PinkSlip.query

    if search_query:
        search_filter = (
            PinkSlip.slip_number.ilike(f'%{search_query}%') |
            PinkSlip.first_initial.ilike(f'%{search_query}%') |
            PinkSlip.last_name.ilike(f'%{search_query}%') |
            PinkSlip.phone.ilike(f'%{search_query}%')
        )
        query = query.filter(search_filter)

    pagination = query.order_by(PinkSlip.slip_number).paginate(page=page, per_page=per_page, error_out=False)
    slips = pagination.items

    html = "<h1>All Pink Slips</h1>"
    html += '<a href="/"><button type="button">Back to Upload</button></a><br><br>'

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
        html += f"<p><i>Showing results for: '{search_query}' ({pagination.total} slip(s) found)</i></p>"

    html += f"<p>Showing {len(slips)} of {pagination.total} slips (Page {pagination.page} of {pagination.pages})</p>"

    if not slips:
        return html + "<p>No records found.</p>"

    for t in slips:
        html += (
            f"<h2>Slip: {t.slip_number} | Customer: {t.first_initial}. {t.last_name} | Phone: {t.phone}</h2>"
            f"<p>Date Received: {t.date_received or 'N/A'} | Due: {t.due_date or 'N/A'}"
            f"{(' at ' + t.due_time) if t.due_time else ''} | Total: ${t.total_amount:.2f}</p>"
        )
        html += "<ul>"
        for it in t.items:
            desc = f" - {it.work_description}" if it.work_description else ""
            html += f"<li>{it.item_type}{desc} - ${it.price:.2f}</li>"
        html += "</ul>"

    search_param = f"&search={search_query}" if search_query else ""
    html += '<div style="margin-top: 20px;">'
    if pagination.has_prev:
        html += f'<a href="/records?page={pagination.prev_num}{search_param}"><button type="button">&laquo; Previous</button></a> '
    for p in pagination.iter_pages(left_edge=1, right_edge=1, left_current=2, right_current=2):
        if p is None:
            html += ' ... '
        elif p == pagination.page:
            html += f' <b>[{p}]</b> '
        else:
            html += f' <a href="/records?page={p}{search_param}">{p}</a> '
    if pagination.has_next:
        html += f' <a href="/records?page={pagination.next_num}{search_param}"><button type="button">Next &raquo;</button></a>'
    html += '</div>'

    return html

@app.route("/add_pink_slip", methods=["GET", "POST"])
def add_pink_slip():
    if request.method == "POST":
        slip_number = request.form.get("slip_number", "").strip()
        first_initial = request.form.get("first_initial", "").strip().upper()[:1]
        last_name = request.form.get("last_name", "").strip()
        phone = _format_phone(request.form.get("phone", ""))
        date_received = _format_date_val(request.form.get("date_received", ""))
        due_date = _format_date_val(request.form.get("due_date", ""))
        due_time = _parse_time(request.form.get("due_time", ""))

        item_types_raw = request.form.getlist("item_type")
        work_descriptions = request.form.getlist("work_description")
        prices_raw = request.form.getlist("price")

        if not item_types_raw:
            return "At least one item is required.", 400

        validated_items = []
        for i, (it_raw, wd, pr) in enumerate(zip(item_types_raw, work_descriptions, prices_raw), start=1):
            item_type, item_type_valid = _normalize_item_type(it_raw.strip())
            if not item_type_valid:
                return f"Item {i}: Invalid item type. Valid options: {', '.join(VALID_ITEM_TYPES)}", 400
            price_clean = pr.strip().replace('$', '').replace(',', '')
            try:
                price = float(price_clean)
                if price < 0:
                    raise ValueError
            except ValueError:
                return f"Item {i}: Invalid price. Must be a positive number.", 400
            validated_items.append((item_type, wd.strip(), price))

        pink_slip = PinkSlip.query.filter_by(slip_number=slip_number).first()
        if not pink_slip:
            pink_slip = PinkSlip(
                slip_number=slip_number,
                first_initial=first_initial or '?',
                last_name=last_name or 'Unknown',
                phone=phone,
                date_received=date_received,
                due_date=due_date,
                due_time=due_time,
                total_amount=0.0
            )
            db.session.add(pink_slip)

        for item_type, work_description, price in validated_items:
            item = PinkSlipItem(
                slip=pink_slip,
                item_type=item_type,
                work_description=work_description,
                price=price
            )
            db.session.add(item)

        pink_slip.total_amount = sum(it.price for it in pink_slip.items)
        db.session.commit()

        return f"Pink slip {slip_number} added successfully with {len(validated_items)} item(s)! <a href='/records'>View Records</a>"

    item_options = ''.join(f'<option value="{t}">{t}</option>' for t in VALID_ITEM_TYPES)

    return f"""
    <h1>Add Pink Slip</h1>
    <form method="POST">
        <fieldset>
            <legend>Slip Info</legend>
            Slip Number: <input type="text" name="slip_number" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" oninput="this.value=this.value.replace(/[^0-9]/g,'')" required><br>
            First Initial: <input type="text" name="first_initial" maxlength="1" pattern="[A-Za-z]" title="One letter only" oninput="this.value=this.value.replace(/[^A-Za-z]/g,'')" required><br>
            Last Name: <input type="text" name="last_name" pattern="[A-Za-z \\-']+" title="Letters, spaces, hyphens, and apostrophes only" oninput="this.value=this.value.replace(/[^A-Za-z \\-']/g,'')" required><br>
            Phone: <input type="text" name="phone" pattern="[0-9()\\- ]+" title="Numbers, parentheses, and dashes only" oninput="this.value=this.value.replace(/[^0-9()\\- ]/g,'')" required><br>
            Date Received: <input type="date" name="date_received" required><br>
            Due Date: <input type="date" name="due_date" required><br>
            Due Time: <input type="time" name="due_time"><br>
        </fieldset>
        <fieldset>
            <legend>Items</legend>
            <div id="items-container">
                <div class="item-row">
                    Item Type: <select name="item_type" required>{item_options}</select>
                    Work Description: <input type="text" name="work_description">
                    Price: <input type="text" name="price" inputmode="decimal" oninput="this.value=this.value.replace(/[^0-9.]/g,'')" required>
                </div>
            </div>
            <br>
            <button type="button" onclick="addItem()">+ Add Another Item</button>
        </fieldset>
        <br>
        <input type="submit" value="Add Pink Slip">
    </form>
    <br>
    <a href="/"><button type="button">Back to Home</button></a>
    <script>
    function addItem() {{
        var container = document.getElementById('items-container');
        var row = document.createElement('div');
        row.className = 'item-row';
        row.style.marginTop = '8px';
        row.innerHTML = 'Item Type: <select name="item_type" required>{item_options}</select> '
            + 'Work Description: <input type="text" name="work_description"> '
            + 'Price: <input type="text" name="price" inputmode="decimal" required> '
            + '<button type="button" onclick="this.parentElement.remove()">Remove</button>';
        var priceInput = row.querySelector('input[name="price"]');
        priceInput.addEventListener('input', function() {{
            this.value = this.value.replace(/[^0-9.]/g, '');
        }});
        container.appendChild(row);
    }}
    </script>
    """

@app.route("/export")
def export():
    slips_df = pd.read_sql_query('SELECT * FROM pink_slip', db.engine)
    items_df = pd.read_sql_query('SELECT * FROM pink_slip_item', db.engine)

    merged = items_df.merge(slips_df, left_on='slip_id', right_on='id', suffixes=('_item', '_slip'))
    export_df = merged[['slip_number', 'first_initial', 'last_name', 'phone',
                         'date_received', 'due_date', 'due_time', 'item_type',
                         'work_description', 'price', 'total_amount']]

    csv_data = export_df.to_csv(index=False)
    return Response(csv_data, mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=pink_slips.csv'})

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
