import os
import random
from types import SimpleNamespace
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_sqlalchemy import SQLAlchemy
import sqlalchemy
from sqlalchemy import func, case, text
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
import logging
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --- APP INITIALIZATION ---
load_dotenv()
app = Flask(__name__)

# --- DATABASE CONFIGURATION ---
DB_USER = os.environ.get('DB_USER', 'root')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'root')
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_NAME = os.environ.get('DB_NAME', 'orphanage_db')

# DATABASE_URL (e.g. sqlite:// for tests) overrides the MySQL settings above.
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or f'mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- SECURITY: require a real secret key (no insecure fallback) ---
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key or _secret_key == 'a-secret-key-you-should-change':
    raise RuntimeError(
        "CRITICAL: A secure SECRET_KEY environment variable must be configured."
    )
app.config['SECRET_KEY'] = _secret_key

# --- SECURITY: session cookie hardening ---
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    # Secure=True requires HTTPS; enable via env in production (would break plain HTTP).
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() in ('true', '1'),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

logging.basicConfig(level=logging.INFO)

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
csrf = CSRFProtect(app)
# Rate limiting: no global default (avoids throttling normal browsing);
# explicit limits are attached to sensitive routes below.
limiter = Limiter(get_remote_address, app=app)

# --- LOGIN MANAGER SETUP ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return Account.query.get(int(user_id))


# --- DATABASE MODELS ---
class Account(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(60), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'donor', 'ngo' or 'admin'
    area = db.Column(db.String(60), nullable=True) # SmartMatch: donor's home area in Pune
    orphanage = db.relationship('Orphanage', back_populates='account', uselist=False)
    pledges = db.relationship('ItemNeeded', back_populates='donor')

class Orphanage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    city = db.Column(db.String(100), nullable=False)
    address = db.Column(db.Text, nullable=False)
    contact_email = db.Column(db.String(120), unique=True, nullable=False)
    contact_phone = db.Column(db.String(20), nullable=True) # SmartMatch: WhatsApp alerts
    description = db.Column(db.Text, nullable=True)
    items = db.relationship('ItemNeeded', back_populates='orphanage', cascade="all, delete-orphan")
    updates = db.relationship('Update', back_populates='orphanage', cascade="all, delete-orphan")
    surpluses = db.relationship('SurplusItem', back_populates='orphanage', cascade="all, delete-orphan")
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), unique=True)
    account = db.relationship('Account', back_populates='orphanage')
    children_0_5 = db.Column(db.Integer, default=0)
    children_6_12 = db.Column(db.Integer, default=0)
    children_13_18 = db.Column(db.Integer, default=0)

class ItemNeeded(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    quantity_needed = db.Column(db.Integer, nullable=False)
    is_urgent = db.Column(db.Boolean, default=False, nullable=False)
    date_posted = db.Column(db.DateTime, server_default=db.func.now())
    status = db.Column(db.String(20), default='Active')
    orphanage_id = db.Column(db.Integer, db.ForeignKey('orphanage.id'), nullable=False)
    orphanage = db.relationship('Orphanage', back_populates='items')
    pledge_timestamp = db.Column(db.DateTime, nullable=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=True)
    donor = db.relationship('Account', back_populates='pledges')
    # SmartMatch need-freshness tracking. last_updated is set explicitly (no onupdate)
    # so that the stale scan writing is_stale does not count as the NGO refreshing it.
    last_updated = db.Column(db.DateTime, server_default=db.func.now())
    is_stale = db.Column(db.Boolean, default=False, nullable=False, server_default=sqlalchemy.false())
    stale_since = db.Column(db.DateTime, nullable=True)

class Update(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    orphanage_id = db.Column(db.Integer, db.ForeignKey('orphanage.id'), nullable=False)
    orphanage = db.relationship('Orphanage', back_populates='updates')

class SurplusItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='Available')
    orphanage_id = db.Column(db.Integer, db.ForeignKey('orphanage.id'), nullable=False)
    orphanage = db.relationship('Orphanage', back_populates='surpluses')

class AlertLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    orphanage_id = db.Column(db.Integer, db.ForeignKey('orphanage.id', ondelete='CASCADE'), nullable=False)
    orphanage = db.relationship('Orphanage')
    channel = db.Column(db.String(20), nullable=False)   # 'email' or 'whatsapp'
    recipient = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(20), nullable=False)    # 'sent', 'failed' or 'dry_run'
    item_count = db.Column(db.Integer, default=0)
    message = db.Column(db.Text, nullable=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


# --- GLOBAL ADVANCED QUERY FUNCTIONS ---
def get_needs_status_report():
    report = db.session.query(
        Orphanage.name.label('orphanage_name'),
        Orphanage.city,
        func.count(ItemNeeded.id).label('total_items'),
        func.sum(case((ItemNeeded.status == 'Active', 1), else_=0)).label('active_needs'),
        func.sum(case((ItemNeeded.status == 'Pledged', 1), else_=0)).label('pledged_needs'),
        func.sum(case((ItemNeeded.status == 'Fulfilled', 1), else_=0)).label('fulfilled_needs')
    ).outerjoin(ItemNeeded).group_by(Orphanage.id).order_by(
        Orphanage.name
    ).all()
    return report

def get_urgent_needs_from_engaged_orphanages():
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    engaged_orphanage_ids = db.session.query(
        Update.orphanage_id
    ).filter(
        Update.date_posted >= thirty_days_ago
    ).distinct()
    urgent_items = db.session.query(
        ItemNeeded.item_name,
        ItemNeeded.quantity_needed,
        Orphanage.name.label('orphanage_name')
    ).join(Orphanage).filter(
        ItemNeeded.is_urgent == True,
        ItemNeeded.status == 'Active',
        Orphanage.id.in_(engaged_orphanage_ids)
    ).all()
    return urgent_items

def get_top_3_categories():
    top_categories = db.session.query(
        ItemNeeded.category,
        func.sum(ItemNeeded.quantity_needed).label('total_quantity_demanded')
    ).filter(
        ItemNeeded.status == 'Active'
    ).group_by(
        ItemNeeded.category
    ).order_by(
        func.sum(ItemNeeded.quantity_needed).desc()
    ).limit(3).all()
    return top_categories

def get_orphanage_category_ranking():
    raw_query = text("""
        WITH RankedNeeds AS (
            SELECT
                o.name AS orphanage_name, i.category,
                SUM(i.quantity_needed) AS total_quantity,
                RANK() OVER(
                    PARTITION BY i.category
                    ORDER BY SUM(i.quantity_needed) DESC
                ) AS rnk
            FROM item_needed i JOIN orphanage o ON i.orphanage_id = o.id
            WHERE i.status = 'Active'
            GROUP BY o.name, i.category
        )
        SELECT category, orphanage_name, total_quantity
        FROM RankedNeeds
        WHERE rnk <= 3
        ORDER BY category, rnk;
    """)
    result = db.session.execute(raw_query)
    return result.fetchall()

# --- PERSONAL QUERY FUNCTIONS ---
def get_personal_status_report(ngo_id):
    report = db.session.query(
        func.count(ItemNeeded.id).label('total_items'),
        func.sum(case((ItemNeeded.status == 'Active', 1), else_=0)).label('active_needs'),
        func.sum(case((ItemNeeded.status == 'Pledged', 1), else_=0)).label('pledged_needs'),
        func.sum(case((ItemNeeded.status == 'Fulfilled', 1), else_=0)).label('fulfilled_needs')
    ).filter(
        ItemNeeded.orphanage_id == ngo_id
    ).first()
    return report

def get_personal_recent_pledges(ngo_id):
    recent_pledges = db.session.query(
        ItemNeeded.item_name,
        ItemNeeded.pledge_timestamp,
        Account.name.label('donor_name')
    ).join(
        Account, ItemNeeded.donor_id == Account.id
    ).filter(
        ItemNeeded.orphanage_id == ngo_id,
        ItemNeeded.status == 'Pledged'
    ).order_by(
        ItemNeeded.pledge_timestamp.desc()
    ).limit(5).all()
    return recent_pledges


# --- ROUTES ---
@app.route('/')
def home():
    image_files = []
    try:
        image_folder_path = os.path.join(app.static_folder, 'images')
        image_files = [f for f in os.listdir(image_folder_path) if f.endswith(('.jpg', '.jpeg', '.png', '.webp'))]
    except FileNotFoundError:
        flash("Note: 'static/images' folder not found. Cannot display gallery.", "warning")
    except Exception as e:
        print(f"Error loading images: {e}")
    return render_template('home.html', images=image_files)

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/directory')
def directory():
    all_orphanages = Orphanage.query.all()
    return render_template('directory.html', orphanages=all_orphanages)

@app.route('/needs')
def needs_list():
    all_orphanages = Orphanage.query.all()
    return render_template('needs_list.html', orphanages=all_orphanages)

@app.route('/pledge/<int:item_id>', methods=['POST'])
@login_required
@limiter.limit("20 per minute")
def pledge(item_id):
    if current_user.role != 'donor':
        flash('Only donor accounts can make pledges.', 'danger')
        return redirect(url_for('needs_list'))
    # Atomic conditional update: the WHERE status='Active' and the write happen
    # in one SQL statement, so two donors pledging the same item concurrently
    # cannot both succeed and overwrite each other.
    rows = ItemNeeded.query.filter_by(id=item_id, status='Active').update(
        {
            'status': 'Pledged',
            'donor_id': current_user.id,
            'pledge_timestamp': datetime.utcnow(),
        },
        synchronize_session=False,
    )
    db.session.commit()
    if rows == 0:
        flash('This item is no longer active — it may have just been pledged by another donor.', 'warning')
    else:
        flash('Thank you! Your pledge has been recorded in your dashboard.', 'success')
    return redirect(url_for('needs_list'))

@app.route('/register_donor', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=['POST'])
def register_donor():
    if current_user.is_authenticated:
        flash(f"You are already logged in as {current_user.name}.", 'info')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        try:
            hashed_password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
            new_account = Account(
                email=request.form['email'],
                name=request.form['name'],
                password=hashed_password,
                role='donor'
            )
            db.session.add(new_account)
            db.session.commit()
            flash('Donor account created! Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlalchemy.exc.IntegrityError:
            db.session.rollback()
            flash('An account with this email already exists. Please log in.', 'danger')
            return redirect(url_for('register_donor'))
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error in {request.endpoint}: {e}", exc_info=True)
            flash('An unexpected error occurred. Please try again.', 'danger')
            return redirect(url_for('register_donor'))
    return render_template('register_donor.html')

@app.route('/register_ngo', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=['POST'])
def register_ngo():
    if current_user.is_authenticated:
        flash(f"You are already logged in as {current_user.name}.", 'info')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        try:
            hashed_password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
            new_account = Account(
                email=request.form['email'],
                name=request.form['name'],
                password=hashed_password,
                role='ngo'
            )
            db.session.add(new_account)
            new_orphanage = Orphanage(
                name=request.form['orphanage_name'], 
                city=request.form['city'], 
                address=request.form['address'], 
                contact_email=request.form['contact_email'], 
                description=request.form['description'], 
                account=new_account
            )
            db.session.add(new_orphanage)
            db.session.commit()
            flash('Orphanage account created! Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlalchemy.exc.IntegrityError:
            db.session.rollback()
            flash('An account with this email already exists. Please log in.', 'danger')
            return redirect(url_for('register_ngo'))
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error in {request.endpoint}: {e}", exc_info=True)
            flash('An unexpected error occurred. Please try again.', 'danger')
            return redirect(url_for('register_ngo'))
    return render_template('register_ngo.html')

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=['POST'])
def login():
    if current_user.is_authenticated:
        flash(f"You are already logged in as {current_user.name}.", 'info')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        user = Account.query.filter_by(email=request.form['email']).first()
        if user and bcrypt.check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Login failed. Check email and password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'ngo':
        if not current_user.orphanage:
            flash('Your orphanage profile was not found. Please contact support.', 'danger')
            return redirect(url_for('logout'))
        my_items = ItemNeeded.query.filter(
            ItemNeeded.orphanage_id == current_user.orphanage.id,
            ItemNeeded.status.in_(['Active', 'Pledged', 'Fulfilled'])
        ).order_by(ItemNeeded.date_posted.desc()).all()
        my_surplus = SurplusItem.query.filter(
            SurplusItem.orphanage_id == current_user.orphanage.id
        ).order_by(SurplusItem.id.desc()).all()
        return render_template('dashboard.html', items=my_items, surpluses=my_surplus, now=datetime.utcnow())
    elif current_user.role == 'admin':
        return redirect(url_for('smartmatch.dashboard'))
    elif current_user.role == 'donor':
        my_pledges = ItemNeeded.query.filter_by(donor_id=current_user.id).order_by(ItemNeeded.pledge_timestamp.desc()).all()
        return render_template('donor_dashboard.html', pledges=my_pledges)
    else:
        return redirect(url_for('logout'))

# --- [ALL PATHS BELOW ARE NOW CORRECTED] ---

@app.route('/dashboard/stats', methods=['GET', 'POST'])
@login_required
def manage_stats():
    if current_user.role != 'ngo':
        abort(403)
    orphanage = current_user.orphanage
    if request.method == 'POST':
        try:
            orphanage.children_0_5 = int(request.form.get('children_0_5', 0))
            orphanage.children_6_12 = int(request.form.get('children_6_12', 0))
            orphanage.children_13_18 = int(request.form.get('children_13_18', 0))
            db.session.commit()
            flash('Your statistics have been updated!', 'success')
            return redirect(url_for('dashboard'))
        except (ValueError, TypeError):
            db.session.rollback()
            flash('Please enter valid whole numbers for the statistics.', 'danger')
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error in {request.endpoint}: {e}", exc_info=True)
            flash('An unexpected error occurred while updating stats. Please try again.', 'danger')
    
    return render_template('admin/manage_stats.html', orphanage=orphanage)

@app.route('/insights')
@login_required
def insights():
    if current_user.role != 'ngo': abort(403)
    ngo_id = current_user.orphanage.id
    try:
        personal_report_data = get_personal_status_report(ngo_id)
        personal_pledge_data = get_personal_recent_pledges(ngo_id)
        global_report_1 = get_needs_status_report()
        global_report_2 = get_urgent_needs_from_engaged_orphanages()
        global_report_3 = get_top_3_categories()
        global_report_4 = get_orphanage_category_ranking()
    except Exception as e:
        logging.error(f"Error in {request.endpoint}: {e}", exc_info=True)
        flash("An unexpected error occurred while generating reports. Please try again.", 'danger')
        personal_report_data, personal_pledge_data = None, []
        global_report_1, global_report_2, global_report_3, global_report_4 = [], [], [], []
    
    return render_template(
        'admin/insights.html',
        personal_report=personal_report_data,
        recent_pledges=personal_pledge_data,
        report1=global_report_1,
        report2=global_report_2,
        report3=global_report_3,
        report4=global_report_4
    )

@app.route('/add_item', methods=['GET', 'POST'])
@login_required
def add_item():
    if current_user.role != 'ngo': abort(403)
    if request.method == 'POST':
        try:
            qty = int(request.form.get('quantity_needed', 0))
            if qty <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            flash('Quantity must be a positive integer greater than zero.', 'danger')
            return redirect(url_for('add_item'))
        is_urgent = 'is_urgent' in request.form
        new_item = ItemNeeded(
            item_name=request.form['item_name'],
            category=request.form['category'],
            quantity_needed=qty,
            is_urgent=is_urgent,
            orphanage_id=current_user.orphanage.id,
            last_updated=datetime.utcnow()
        )
        db.session.add(new_item)
        db.session.commit()
        flash('New need added successfully.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('admin/add_item.html')

@app.route('/edit_item/<int:item_id>', methods=['GET', 'POST'])
@login_required
def edit_item(item_id):
    if current_user.role != 'ngo': abort(403)
    item_to_edit = ItemNeeded.query.get_or_404(item_id)
    if item_to_edit.orphanage.account != current_user:
        abort(403)
    if request.method == 'POST':
        try:
            qty = int(request.form.get('quantity_needed', 0))
            if qty <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            flash('Quantity must be a positive integer greater than zero.', 'danger')
            return redirect(url_for('edit_item', item_id=item_id))
        item_to_edit.item_name = request.form['item_name']
        item_to_edit.category = request.form['category']
        item_to_edit.quantity_needed = qty
        new_status = request.form['status']
        # Moving a pledged item back to Active must clear the stale donor link,
        # otherwise the old donor stays attached to an item they no longer hold.
        if new_status == 'Active' and item_to_edit.status == 'Pledged':
            item_to_edit.donor_id = None
            item_to_edit.pledge_timestamp = None
        item_to_edit.status = new_status
        item_to_edit.is_urgent = 'is_urgent' in request.form
        item_to_edit.last_updated = datetime.utcnow()
        item_to_edit.is_stale = False
        item_to_edit.stale_since = None
        db.session.commit()
        flash('Item updated successfully.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('admin/edit_item.html', item=item_to_edit)

@app.route('/delete_item/<int:item_id>', methods=['POST'])
@login_required
def delete_item(item_id):
    if current_user.role != 'ngo': abort(403)
    item_to_delete = ItemNeeded.query.get_or_404(item_id)
    if item_to_delete.orphanage.account != current_user:
        abort(403)
    if item_to_delete.status == 'Pledged':
        flash('Cannot delete an item with an active pledge. Fulfil or cancel the pledge first.', 'warning')
        return redirect(url_for('dashboard'))
    db.session.delete(item_to_delete)
    db.session.commit()
    flash('Item deleted.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/add_update', methods=['GET', 'POST'])
@login_required
def add_update():
    if current_user.role != 'ngo': abort(403)
    if request.method == 'POST':
        title = request.form.get('title')
        body = request.form.get('body')
        if title and body:
            new_update = Update(
                title=title,
                body=body,
                orphanage_id=current_user.orphanage.id
            )
            db.session.add(new_update)
            db.session.commit()
            flash('Update posted successfully.', 'success')
            return redirect(url_for('dashboard'))
    return render_template('admin/add_update.html')

@app.route('/add_surplus', methods=['GET', 'POST'])
@login_required
def add_surplus():
    if current_user.role != 'ngo': abort(403)
    if request.method == 'POST':
        try:
            qty = int(request.form.get('quantity', 0))
            if qty <= 0:
                raise ValueError('non-positive quantity')
            new_surplus = SurplusItem(
                item_name=request.form['item_name'],
                category=request.form['category'],
                quantity=qty,
                orphanage_id=current_user.orphanage.id,
                status='Available'
            )
            db.session.add(new_surplus)
            db.session.commit()
            flash('Surplus item added successfully.', 'success')
        except ValueError:
            db.session.rollback()
            flash('Quantity must be a positive integer greater than zero.', 'danger')
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error in {request.endpoint}: {e}", exc_info=True)
            flash('An unexpected error occurred while adding surplus. Please try again.', 'danger')
        return redirect(url_for('dashboard'))
    return render_template('admin/add_surplus.html')

@app.route('/reroute')
@login_required
def reroute():
    if current_user.role != 'ngo': abort(403)
    my_orphanage_id = current_user.orphanage.id
    matches = db.session.query(
        SurplusItem.id.label('my_surplus_id'),
        SurplusItem.item_name,
        SurplusItem.category,
        SurplusItem.quantity.label('my_surplus_quantity'),
        ItemNeeded.id.label('their_need_id'),
        ItemNeeded.quantity_needed.label('their_needed_quantity'),
        Orphanage.name.label('they_need_it_orphanage_name')
    ).join(
        ItemNeeded, 
        (func.lower(SurplusItem.item_name) == func.lower(ItemNeeded.item_name)) &
        (func.lower(SurplusItem.category) == func.lower(ItemNeeded.category))
    ).join(
        Orphanage, 
        ItemNeeded.orphanage_id == Orphanage.id
    ).filter(
        SurplusItem.orphanage_id == my_orphanage_id,
        SurplusItem.status == 'Available',
        ItemNeeded.orphanage_id != my_orphanage_id,
        ItemNeeded.status == 'Active',
        SurplusItem.quantity >= ItemNeeded.quantity_needed
    ).all()
    return render_template('admin/reroute.html', matches=matches)

@app.route('/execute_reroute', methods=['POST'])
@login_required
def execute_reroute():
    if current_user.role != 'ngo': abort(403)
    try:
        surplus_id = int(request.form.get('surplus_id'))
        need_id = int(request.form.get('need_id'))
        surplus_item = SurplusItem.query.get_or_404(surplus_id)
        need_item = ItemNeeded.query.get_or_404(need_id)
        if surplus_item.orphanage.account != current_user:
            flash('Authorization error: This is not your surplus item.', 'danger')
            return redirect(url_for('reroute'))
        if need_item.orphanage_id == current_user.orphanage.id:
            flash("You cannot reroute a surplus into your own orphanage's need.", 'danger')
            return redirect(url_for('reroute'))
        if surplus_item.quantity < need_item.quantity_needed:
            flash('Reroute failed: You no longer have enough quantity.', 'danger')
            return redirect(url_for('reroute'))
        if need_item.status != 'Active':
            flash('Reroute failed: This item is no longer an active need.', 'danger')
            return redirect(url_for('reroute'))
        surplus_item.quantity -= need_item.quantity_needed
        if surplus_item.quantity == 0:
            surplus_item.status = 'Rerouted'
        # A reroute fulfils a need from another orphanage's surplus, not a donor
        # pledge. Do NOT set donor_id (that would mislabel this NGO as the donor).
        need_item.status = 'Fulfilled'
        need_item.pledge_timestamp = datetime.utcnow()
        db.session.commit()
        flash(f"Success! {need_item.quantity_needed} x {need_item.item_name} rerouted to {need_item.orphanage.name}.", 'success')
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error in {request.endpoint}: {e}", exc_info=True)
        flash('An unexpected error occurred during rerouting. Please try again.', 'danger')
    return redirect(url_for('reroute'))


# --- ERROR HANDLERS ---
@app.errorhandler(403)
def forbidden_error(error):
    return render_template('errors/403.html'), 403

@app.errorhandler(404)
def not_found_error(error):
    return render_template('errors/404.html'), 404

@app.errorhandler(429)
def ratelimit_error(error):
    return render_template('errors/429.html'), 429

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('errors/500.html'), 500

@app.errorhandler(CSRFError)
def csrf_error(error):
    return render_template('errors/csrf.html', reason=error.description), 400


# --- DB Setup Command ---
@app.cli.command("create-db")
def create_db():
    """Drops and recreates all database tables."""
    db.drop_all()
    db.create_all()
    print("Database tables created.")


# --- [NEW] SEED DATABASE COMMAND ---
@app.cli.command("seed-db")
def seed_db():
    """Seeds the database with 10 real Pune orphanages and varied data."""
    
    # 0. Clear all existing data
    print("Clearing old data...")
    db.session.query(AlertLog).delete()
    db.session.query(ItemNeeded).delete()
    db.session.query(SurplusItem).delete()
    db.session.query(Update).delete()
    db.session.query(Orphanage).delete()
    db.session.query(Account).delete()
    db.session.commit()

    # 1. Dummy Donor Account (Password: 11)
    try:
        donor_hash = bcrypt.generate_password_hash('11').decode('utf-8')
        donor_account = Account(
            email='donor@example.com',
            name='Test Donor',
            password=donor_hash,
            role='donor',
            area='Kothrud'
        )
        db.session.add(donor_account)
        db.session.commit()
        print("Created dummy donor account (donor@example.com / 11)")
    except Exception as e:
        db.session.rollback()
        print(f"Error creating donor: {e}")

    # 1b. SmartMatch admin account (Password: 11)
    try:
        admin_account = Account(
            email='admin@example.com',
            name='SmartMatch Admin',
            password=bcrypt.generate_password_hash('11').decode('utf-8'),
            role='admin'
        )
        db.session.add(admin_account)
        db.session.commit()
        print("Created admin account (admin@example.com / 11)")
    except Exception as e:
        db.session.rollback()
        print(f"Error creating admin: {e}")

    # 2. Dummy NGO Data (10 Orphanages in Pune)
    ngo_data = [
        {"name": "Bhavishya Sunshine Homes", "city": "Kothrud", "contact": "Mrs. Sharma"},
        {"name": "SOFOSH", "city": "Shivaji Nagar", "contact": "Mr. Kulkarni"},
        {"name": "Bal Kalyan Sanstha", "city": "Deccan Gymkhana", "contact": "Ms. Apte"},
        {"name": "Vatsalya Foundation", "city": "Koregaon Park", "contact": "Mr. Shah"},
        {"name": "Deep Griha Society", "city": "Hadapsar", "contact": "Mrs. Rao"},
        {"name": "Tara Children's Project", "city": "Baner", "contact": "Mr. Gupta"},
        {"name": "Swadhar", "city": "Aundh", "contact": "Ms. Joshi"},
        {"name": "Maher", "city": "Viman Nagar", "contact": "Sister Lucy"},
        {"name": "Hope for the Children Foundation", "city": "Katraj", "contact": "Mr. Singh"},
        {"name": "Tayabia Orphanage", "city": "Camp", "contact": "Mr. Inamdar"}
    ]

    print(f"Creating {len(ngo_data)} dummy NGO accounts...")
    ngo_accounts = []
    
    for i, data in enumerate(ngo_data):
        try:
            ngo_hash = bcrypt.generate_password_hash('11').decode('utf-8')
            email = f"test{i+1}@example.com"
            
            new_account = Account(
                email=email, name=data['contact'], password=ngo_hash, role='ngo'
            )
            
            new_orphanage = Orphanage(
                name=data['name'],
                city=data['city'],
                address=f"123 {data['city']} Main Road, Pune, Maharashtra",
                contact_email=email,
                description=f"{data['name']} is a non-profit organization dedicated to providing a safe and nurturing environment for children in {data['city']}.",
                account=new_account,
                children_0_5 = random.randint(3, 10),
                children_6_12 = random.randint(10, 20),
                children_13_18 = random.randint(5, 15)
            )
            db.session.add(new_account)
            db.session.add(new_orphanage)
            ngo_accounts.append(new_orphanage)

        except Exception as e:
            db.session.rollback()
            print(f"Error creating {data['name']}: {e}")
    
    db.session.commit()
    print("All NGO accounts and profiles created.")

    # 3. [NEW] Dummy Needs and Surplus for ALL Orphanages
    print("Adding dummy needs and surpluses...")
    
    needs_pool = [
        {"name": "Rice Bags (10kg)", "cat": "Food", "qty": 50, "urg": True},
        {"name": "Lentils (Dal) (5kg)", "cat": "Food", "qty": 100},
        {"name": "Notebooks (A4)", "cat": "School Supplies", "qty": 200},
        {"name": "Pencils (Box of 50)", "cat": "School Supplies", "qty": 30},
        {"name": "Blankets (Single)", "cat": "Bedding", "qty": 75},
        {"name": "Children's T-Shirts (Mixed Sizes)", "cat": "Clothing", "qty": 150},
        {"name": "Soap Bars (Pack of 10)", "cat": "Hygiene", "qty": 60},
        {"name": "Toothpaste (Large)", "cat": "Hygiene", "qty": 100},
        {"name": "Diapers (Size M)", "cat": "Hygiene", "qty": 40, "urg": True},
        {"name": "School Bags", "cat": "School Supplies", "qty": 50}
    ]
    
    surplus_pool = [
        {"name": "Winter Jackets (Small)", "cat": "Clothing", "qty": 50},
        {"name": "Canned Goods (Mixed)", "cat": "Food", "qty": 100},
        {"name": "Textbooks (Old Curriculum)", "cat": "School Supplies", "qty": 200},
    ]

    try:
        for ngo in ngo_accounts:
            # Give each NGO 2-3 random needs
            num_needs = random.randint(2, 3)
            items_to_add = random.sample(needs_pool, num_needs)
            
            for item in items_to_add:
                new_need = ItemNeeded(
                    item_name=item['name'],
                    category=item['cat'],
                    quantity_needed=item.get('qty', 50),
                    is_urgent=item.get('urg', False),
                    orphanage_id=ngo.id
                )
                db.session.add(new_need)
            
            # 30% chance of having a surplus item
            if random.random() < 0.3:
                item = random.choice(surplus_pool)
                new_surplus = SurplusItem(
                    item_name=item['name'],
                    category=item['cat'],
                    quantity=item.get('qty', 20),
                    orphanage_id=ngo.id
                )
                db.session.add(new_surplus)

        # --- Create a GUARANTEED Reroute Opportunity ---
        # This is CRITICAL for your demo!
        
        # 1. Bhavishya (ngo_accounts[0]) has a SURPLUS of Blankets
        surplus_blankets = SurplusItem(item_name="Blankets (Single)", category="Bedding", quantity=30, orphanage=ngo_accounts[0])
        
        # 2. SOFOSH (ngo_accounts[1]) NEEDS Blankets
        need_blankets = ItemNeeded(item_name="Blankets (Single)", category="Bedding", quantity_needed=25, orphanage=ngo_accounts[1])
        
        db.session.add_all([surplus_blankets, need_blankets])
        db.session.commit()
        print("Added varied needs and surpluses for all orphanages.")

    except Exception as e:
        db.session.rollback()
        print(f"Error adding items: {e}")

    print("\n--- Database Seeding Complete! ---")
    print("Created 1 Donor: donor@example.com / 11")
    print("Created 10 NGOs with password '11'. Log in with:")
    print("  - test1@example.com (Bhavishya)")
    print("  - test2@example.com (SOFOSH)")
    print("  - ...and so on up to test10@example.com")

# --- SMARTMATCH (recommendations, need freshness, alerts) ---
from smartmatch import init_smartmatch
init_smartmatch(app, db, SimpleNamespace(
    Account=Account, Orphanage=Orphanage, ItemNeeded=ItemNeeded,
    Update=Update, SurplusItem=SurplusItem, AlertLog=AlertLog,
))
# The JSON API authenticates with X-API-Key, so its clients cannot carry a CSRF
# token; exempt it (the browser dashboard forms keep CSRF protection).
csrf.exempt(app.blueprints['smartmatch_api'])

if __name__ == '__main__':
    is_debug = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1')
    app.run(debug=is_debug)