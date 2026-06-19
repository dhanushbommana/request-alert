import os
import sqlite3
import smtplib
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import requests
import json
from dotenv import load_dotenv
from email.mime.base import MIMEBase
from email import encoders

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-key-123')

# ============ DATABASE ============
def get_db():
    conn = sqlite3.connect('instance/whatsapp.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                google_id TEXT UNIQUE,
                email TEXT UNIQUE,
                password_hash TEXT,
                name TEXT,
                picture TEXT,
                college_name TEXT,
                college_email TEXT,
                year INTEGER,
                is_admin INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                email TEXT,
                need TEXT,
                issue TEXT,
                status TEXT DEFAULT 'pending',
                response TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                responded_at TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')

        columns = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
        if 'password_hash' not in columns:
            conn.execute('ALTER TABLE users ADD COLUMN password_hash TEXT')
        if 'created_at' not in columns:
            conn.execute('ALTER TABLE users ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')

        # add attachments columns to requests table if missing
        req_cols = [row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()]
        if 'attachments' not in req_cols:
            conn.execute('ALTER TABLE requests ADD COLUMN attachments TEXT')
        if 'response_attachments' not in req_cols:
            conn.execute('ALTER TABLE requests ADD COLUMN response_attachments TEXT')

        # alerts table for admin broadcasts
        alert_cols = [row[1] for row in conn.execute("PRAGMA table_info(alerts)").fetchall()] if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'").fetchone() else []
        if not alert_cols:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    message TEXT,
                    attachments TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        # per-user alert read tracking
        alert_reads_cols = [row[1] for row in conn.execute("PRAGMA table_info(alert_reads)").fetchall()] if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alert_reads'").fetchone() else []
        if not alert_reads_cols:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS alert_reads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id INTEGER,
                    user_id INTEGER,
                    is_read INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(alert_id) REFERENCES alerts(id),
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            ''')

        conn.commit()

        # ensure upload directory exists
        os.makedirs(os.path.join('instance', 'uploads'), exist_ok=True)

        # Seed default admin accounts (only if they do not already exist)
        admins_to_seed = [
            ('admin123@example.com', 'Admin 123', '99230040501'),
            ('admin123@gmail.com', 'Admin 123', '12345678')
        ]
        for email, name, pwd in admins_to_seed:
            exists = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
            if not exists:
                conn.execute(
                    'INSERT INTO users (email, password_hash, name, year, is_admin) VALUES (?, ?, ?, ?, 1)',
                    (email, generate_password_hash(pwd), name, 0)
                )
        conn.commit()

init_db()

# ============ HELPERS ============
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Admin access required', 'danger')
            return redirect(url_for('profile'))
        return f(*args, **kwargs)
    return decorated

def get_user(user_id):
    with get_db() as conn:
        return conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

# ============ EMAIL ============
def send_email(to, subject, html, text="", attachments=None):
    try:
        attachments = attachments or []
        msg = MIMEMultipart('mixed')
        alt = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = os.getenv('MAIL_USERNAME')
        msg['To'] = to
        alt.attach(MIMEText(text, 'plain'))
        alt.attach(MIMEText(html, 'html'))
        msg.attach(alt)

        # attach files
        for path in attachments:
            try:
                part = MIMEBase('application', 'octet-stream')
                with open(path, 'rb') as f:
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(path)}"')
                msg.attach(part)
            except Exception:
                continue

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        try:
            server.login(os.getenv('MAIL_USERNAME'), os.getenv('MAIL_PASSWORD'))
            server.send_message(msg)
            server.quit()
            return True
        except Exception as e:
            print('Error sending email:', e)
            try:
                server.quit()
            except Exception:
                pass
            return False
    except Exception as e:
        print('Unexpected error in send_email:', e)
        return False

def notify_admin(request_data):
    html = f"""
    <h2>New Request #{request_data['id']}</h2>
    <p><b>From:</b> {request_data['name']} ({request_data['email']})</p>
    <p><b>Need:</b> {request_data['need']}</p>
    <p><b>Issue:</b> {request_data['issue']}</p>
    <p><a href="http://localhost:5000/dashboard">View Dashboard</a></p>
    """
    admins_env = [a.strip() for a in os.getenv('ADMIN_EMAILS', '').split(',') if a.strip()]
    admins = admins_env
    if not admins:
        # fallback: query DB for users with is_admin
        with get_db() as conn:
            rows = conn.execute('SELECT email FROM users WHERE is_admin=1').fetchall()
            admins = [r['email'] for r in rows if r['email']]
    for admin in admins:
        send_email(admin, f"🔔 New Request #{request_data['id']}", html, attachments=request_data.get('attachments_paths', []))


@app.route('/admin/alert', methods=['POST'])
@login_required
@admin_required
def send_alert():
    title = request.form.get('title') or 'Admin Alert'
    message = request.form.get('message') or ''
    target = request.form.get('target') or 'all'
    files = request.files.getlist('alert_attachments') if 'alert_attachments' in request.files else []
    saved = []
    for f in files:
        if f and f.filename:
            filename = f"alert_{int(datetime.now().timestamp())}_{f.filename}"
            dest = os.path.join('instance', 'uploads', filename)
            f.save(dest)
            saved.append(dest)
    with get_db() as conn:
        cur = conn.execute('INSERT INTO alerts (title, message, attachments) VALUES (?, ?, ?)', (title, message, json.dumps(saved) if saved else None))
        conn.commit()
        alert_id = cur.lastrowid

        # determine recipients based on target
        if target == 'admins':
            rows = conn.execute('SELECT id, email FROM users WHERE is_admin=1 AND email IS NOT NULL').fetchall()
        else:
            rows = conn.execute('SELECT id, email FROM users WHERE email IS NOT NULL').fetchall()
        recipients = [(r['id'], r['email']) for r in rows if r['email']]

        # create alert_read entries for recipients
        for uid, _ in recipients:
            conn.execute('INSERT INTO alert_reads (alert_id, user_id, is_read) VALUES (?, ?, ?)', (alert_id, uid, 0))
        conn.commit()

    html = f"""
    <h2>{title}</h2>
    <p>{message}</p>
    <p><a href=\"http://localhost:5000/my-requests\">Open App</a></p>
    """

    # send emails to recipients
    for _, email in recipients:
        send_email(email, f"📣 {title}", html, attachments=saved)

    flash('Alert sent to selected users', 'success')
    return redirect(url_for('admin_portal'))

@app.route('/test-email')
def test_email():
    mail_user = os.getenv('MAIL_USERNAME')
    if not mail_user or not os.getenv('MAIL_PASSWORD'):
        return 'MAIL_USERNAME and MAIL_PASSWORD must be set in .env to send test emails.', 400
    success = send_email(mail_user, 'Test Email from Request Alert App', '<p>This is a test email.</p>', text='Test email')
    return ('Test email sent successfully.' if success else 'Failed to send test email. Check server logs for details.'), (200 if success else 500)

def notify_user(email, name, request_id, response, attachments_paths=None):
    attachments_paths = attachments_paths or []
    html = f"""
    <h2>Response to Your Request #{request_id}</h2>
    <p>Hello {name},</p>
    <p><b>Admin Response:</b></p>
    <p style="background:#f0f0f0;padding:15px;border-radius:5px;">{response}</p>
    <p><a href="http://localhost:5000/my-requests">View All Requests</a></p>
    """
    send_email(email, f"✅ Response to Request #{request_id}", html, attachments=attachments_paths)

@app.context_processor
def inject_now():
    return {'now': datetime.now().strftime('%b %d, %Y %I:%M %p')}

# ============ GOOGLE LOGIN ============
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard' if session.get('is_admin') else 'profile'))
    return render_template('login.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        if not email or not password:
            flash('Please enter your email and password.', 'danger')
            return redirect(url_for('index'))

        with get_db() as conn:
            user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
            if user and user['password_hash'] and check_password_hash(user['password_hash'], password):
                session['user_id'] = user['id']
                session['is_admin'] = bool(user['is_admin'])
                return redirect(url_for('dashboard' if session['is_admin'] else 'profile'))

        flash('Email or password is invalid.', 'danger')
        return redirect(url_for('index'))

    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    name = request.form.get('signup_name')
    email = request.form.get('signup_email')
    password = request.form.get('signup_password')
    year = request.form.get('signup_year')

    if not name or not email or not password or not year:
        flash('Please complete all signup fields.', 'danger')
        return redirect(url_for('index'))

    with get_db() as conn:
        existing = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
        if existing:
            flash('This email is already registered. Please log in.', 'warning')
            return redirect(url_for('index'))

        password_hash = generate_password_hash(password)
        is_admin = email.lower() in [a.strip().lower() for a in os.getenv('ADMIN_EMAILS', '').split(',') if a.strip()]
        cur = conn.execute(
            'INSERT INTO users (email, password_hash, name, year, is_admin) VALUES (?, ?, ?, ?, ?)',
            (email, password_hash, name, year, 1 if is_admin else 0)
        )
        conn.commit()
        user_id = cur.lastrowid

    session['user_id'] = user_id
    session['is_admin'] = bool(is_admin)
    flash('Signup complete. Welcome!', 'success')
    return redirect(url_for('dashboard' if session['is_admin'] else 'profile'))

@app.route('/google-login')
def google_login():
    discovery = requests.get('https://accounts.google.com/.well-known/openid-configuration').json()
    auth_url = discovery['authorization_endpoint']
    params = {
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': url_for('callback', _external=True),
        'response_type': 'code',
        'scope': 'openid email profile',
        'access_type': 'offline',
        'prompt': 'select_account'
    }
    return redirect(f"{auth_url}?{urllib.parse.urlencode(params)}")

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return 'Login failed', 400
    
    discovery = requests.get('https://accounts.google.com/.well-known/openid-configuration').json()
    token_url = discovery['token_endpoint']
    
    data = {
        'code': code,
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri': url_for('callback', _external=True),
        'grant_type': 'authorization_code'
    }
    
    token_resp = requests.post(token_url, data=data).json()
    if 'access_token' not in token_resp:
        return 'Login failed', 400
    
    userinfo = requests.get(
        discovery['userinfo_endpoint'],
        headers={'Authorization': f"Bearer {token_resp['access_token']}"}
    ).json()
    
    if not userinfo.get('email_verified'):
        return 'Email not verified', 400
    
    with get_db() as conn:
        user = conn.execute('SELECT * FROM users WHERE google_id = ?', (userinfo['sub'],)).fetchone()
        if not user:
            is_admin = userinfo['email'] in os.getenv('ADMIN_EMAILS', '').split(',')
            cur = conn.execute(
                'INSERT INTO users (google_id, email, name, picture, is_admin) VALUES (?, ?, ?, ?, ?)',
                (userinfo['sub'], userinfo['email'], userinfo.get('name'), userinfo.get('picture'), 1 if is_admin else 0)
            )
            user_id = cur.lastrowid
        else:
            user_id = user['id']
            conn.execute('UPDATE users SET picture = ? WHERE id = ?', (userinfo.get('picture'), user_id))
        conn.commit()
    
    session['user_id'] = user_id
    session['is_admin'] = bool(user['is_admin']) if user else is_admin
    
    return redirect(url_for('dashboard' if session['is_admin'] else 'profile'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# ============ PROFILE ============
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = get_user(session['user_id'])
    if request.method == 'POST':
        with get_db() as conn:
            conn.execute(
                'UPDATE users SET college_name=?, college_email=?, year=? WHERE id=?',
                (request.form.get('college_name'), request.form.get('college_email'), 
                 request.form.get('year'), session['user_id'])
            )
            conn.commit()
        flash('Profile updated!', 'success')
        return redirect(url_for('profile'))

    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM requests WHERE user_id = ? ORDER BY created_at DESC LIMIT 5',
            (session['user_id'],)
        ).fetchall()
        recent_requests = []
        for r in rows:
            d = dict(r)
            try:
                raw = json.loads(d.get('attachments') or '[]')
                d['attachments_list'] = [os.path.basename(p) for p in raw]
            except Exception:
                d['attachments_list'] = []
            recent_requests.append(d)
        # build chart data for profile (status counts)
        stats_rows = conn.execute('SELECT status, COUNT(*) as cnt FROM requests WHERE user_id = ? GROUP BY status', (session['user_id'],)).fetchall()
        status_counts = {'pending': 0, 'responded': 0}
        for s in stats_rows:
            status_counts[s['status']] = s['cnt']
        chart_data_user = json.dumps({
            'labels': ['Pending', 'Responded'],
            'data': [status_counts.get('pending', 0), status_counts.get('responded', 0)]
        })

        # time-series for last 14 days for this user
        days = []
        counts = []
        for i in range(13, -1, -1):
            day = (datetime.now() - timedelta(days=i)).date()
            days.append(day.strftime('%b %d'))
            row = conn.execute('SELECT COUNT(*) as c FROM requests WHERE user_id = ? AND DATE(created_at)= ?', (session['user_id'], day.strftime('%Y-%m-%d'))).fetchone()
            counts.append(row['c'] if row else 0)
        chart_timeseries_user = json.dumps({'labels': days, 'data': counts})

        # fetch recent alerts for this user with read state
        alert_rows = conn.execute('''
            SELECT alerts.*, alert_reads.is_read FROM alerts
            JOIN alert_reads ON alert_reads.alert_id = alerts.id
            WHERE alert_reads.user_id = ?
            ORDER BY alerts.created_at DESC LIMIT 6
        ''', (session['user_id'],)).fetchall()
        alerts = []
        for a in alert_rows:
            ad = dict(a)
            try:
                raw = json.loads(ad.get('attachments') or '[]')
                ad['attachments_list'] = [os.path.basename(p) for p in raw]
            except Exception:
                ad['attachments_list'] = []
            ad['is_read'] = bool(ad.get('is_read'))
            alerts.append(ad)

    return render_template('profile.html', user=user, recent_requests=recent_requests, chart_data_user=chart_data_user, chart_timeseries_user=chart_timeseries_user, alerts=alerts)

# ============ ADMIN PORTAL ============
@app.route('/admin')
@login_required
@admin_required
def admin_portal():
    with get_db() as conn:
        totals = conn.execute('''
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status = 'responded' THEN 1 ELSE 0 END) AS responded
            FROM requests
        ''').fetchone()
        rows = conn.execute('SELECT * FROM requests ORDER BY created_at DESC LIMIT 5').fetchall()
        latest = []
        for r in rows:
            d = dict(r)
            try:
                raw = json.loads(d.get('attachments') or '[]')
                d['attachments_list'] = [os.path.basename(p) for p in raw]
            except Exception:
                d['attachments_list'] = []
            latest.append(d)

        # recent alerts (admin view shows all)
        alert_rows = conn.execute('SELECT * FROM alerts ORDER BY created_at DESC LIMIT 6').fetchall()
        alerts = []
        for a in alert_rows:
            ad = dict(a)
            try:
                raw = json.loads(ad.get('attachments') or '[]')
                ad['attachments_list'] = [os.path.basename(p) for p in raw]
            except Exception:
                ad['attachments_list'] = []
            # admin view doesn't include per-user read flag
            ad['is_read'] = False
            alerts.append(ad)

        # chart data for admin portal (status counts)
        chart_data_admin = json.dumps({
            'labels': ['Pending', 'Responded'],
            'data': [int(totals['pending'] or 0), int(totals['responded'] or 0)]
        })

        # admin time-series (last 14 days across all users)
        days_a = []
        counts_a = []
        for i in range(13, -1, -1):
            day = (datetime.now() - timedelta(days=i)).date()
            days_a.append(day.strftime('%b %d'))
            row = conn.execute('SELECT COUNT(*) as c FROM requests WHERE DATE(created_at)= ?', (day.strftime('%Y-%m-%d'),)).fetchone()
            counts_a.append(row['c'] if row else 0)
        chart_timeseries_admin = json.dumps({'labels': days_a, 'data': counts_a})

    return render_template('admin_portal.html', totals=totals, latest=latest, chart_data_admin=chart_data_admin, chart_timeseries_admin=chart_timeseries_admin, alerts=alerts)

# ============ REQUESTS ============
@app.route('/request', methods=['GET', 'POST'])
@login_required
def make_request():
    user = get_user(session['user_id'])
    if request.method == 'POST':
        data = {
            'user_id': session['user_id'],
            'name': request.form['name'],
            'email': request.form['email'],
            'need': request.form['need'],
            'issue': request.form['issue']
        }
        # handle attachments
        files = request.files.getlist('attachments') if 'attachments' in request.files else []
        saved_paths = []
        for f in files:
            if f and f.filename:
                filename = f"{int(datetime.now().timestamp())}_{f.filename}"
                dest = os.path.join('instance', 'uploads', filename)
                f.save(dest)
                saved_paths.append(dest)

        with get_db() as conn:
            cur = conn.execute(
                'INSERT INTO requests (user_id, name, email, need, issue, attachments) VALUES (?, ?, ?, ?, ?, ?)',
                (data['user_id'], data['name'], data['email'], data['need'], data['issue'], json.dumps(saved_paths) if saved_paths else None)
            )
            request_id = cur.lastrowid
            conn.commit()
        data['id'] = request_id
        data['attachments_paths'] = saved_paths
        notify_admin(data)
        flash('Request submitted! Check your email.', 'success')
        return redirect(url_for('my_requests'))
    return render_template('request.html', user=user)

@app.route('/my-requests')
@login_required
def my_requests():
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM requests WHERE user_id = ? ORDER BY created_at DESC',
            (session['user_id'],)
        ).fetchall()
        requests = []
        for r in rows:
            d = dict(r)
            try:
                raw = json.loads(d.get('attachments') or '[]')
                d['attachments_list'] = [os.path.basename(p) for p in raw]
            except Exception:
                d['attachments_list'] = []
            try:
                raw2 = json.loads(d.get('response_attachments') or '[]')
                d['response_attachments_list'] = [os.path.basename(p) for p in raw2]
            except Exception:
                d['response_attachments_list'] = []
            requests.append(d)
        # fetch recent alerts for this user with read state
        alert_rows = conn.execute('''
            SELECT alerts.*, alert_reads.is_read FROM alerts
            JOIN alert_reads ON alert_reads.alert_id = alerts.id
            WHERE alert_reads.user_id = ?
            ORDER BY alerts.created_at DESC LIMIT 6
        ''', (session['user_id'],)).fetchall()
        alerts = []
        for a in alert_rows:
            ad = dict(a)
            try:
                raw = json.loads(ad.get('attachments') or '[]')
                ad['attachments_list'] = [os.path.basename(p) for p in raw]
            except Exception:
                ad['attachments_list'] = []
            ad['is_read'] = bool(ad.get('is_read'))
            alerts.append(ad)
    return render_template('my_requests.html', requests=requests, alerts=alerts)

# ============ ADMIN ============
@app.route('/dashboard')
@login_required
@admin_required
def dashboard():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM requests ORDER BY created_at DESC').fetchall()
        all_requests = []
        for r in rows:
            d = dict(r)
            try:
                raw = json.loads(d.get('attachments') or '[]')
                d['attachments_list'] = [os.path.basename(p) for p in raw]
            except Exception:
                d['attachments_list'] = []
            try:
                raw2 = json.loads(d.get('response_attachments') or '[]')
                d['response_attachments_list'] = [os.path.basename(p) for p in raw2]
            except Exception:
                d['response_attachments_list'] = []
            all_requests.append(d)

        pending = conn.execute('SELECT COUNT(*) as count FROM requests WHERE status="pending"').fetchone()['count']
        responded = conn.execute('SELECT COUNT(*) as count FROM requests WHERE status="responded"').fetchone()['count']
    return render_template('dashboard.html', requests=all_requests, pending=pending, responded=responded)

@app.route('/respond/<int:request_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def respond(request_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM requests WHERE id = ?', (request_id,)).fetchone()
        if not row:
            flash('Request not found', 'danger')
            return redirect(url_for('dashboard'))
        req = dict(row)
        
        if request.method == 'POST':
            response = request.form['response']
            # handle response attachments
            files = request.files.getlist('response_attachments') if 'response_attachments' in request.files else []
            saved_resp_paths = []
            for f in files:
                if f and f.filename:
                    filename = f"resp_{int(datetime.now().timestamp())}_{f.filename}"
                    dest = os.path.join('instance', 'uploads', filename)
                    f.save(dest)
                    saved_resp_paths.append(dest)

            conn.execute(
                'UPDATE requests SET response=?, status="responded", responded_at=CURRENT_TIMESTAMP, response_attachments=? WHERE id=?',
                (response, json.dumps(saved_resp_paths) if saved_resp_paths else None, request_id)
            )
            conn.commit()
            # email the user with response and attachments
            notify_user(req['email'], req['name'], request_id, response, attachments_paths=saved_resp_paths)
            flash('Response sent!', 'success')
            return redirect(url_for('dashboard'))
    return render_template('respond.html', request=req)


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    uploads_dir = os.path.join('instance', 'uploads')
    return send_from_directory(uploads_dir, filename, as_attachment=True)


@app.route('/alerts/mark_read/<int:alert_id>', methods=['POST'])
@login_required
def mark_alert_read(alert_id):
    user_id = session['user_id']
    with get_db() as conn:
        conn.execute('UPDATE alert_reads SET is_read=1 WHERE alert_id=? AND user_id=?', (alert_id, user_id))
        conn.commit()
    return jsonify({'ok': True})


@app.route('/alerts/unread_count')
@login_required
def alerts_unread_count():
    user_id = session['user_id']
    with get_db() as conn:
        row = conn.execute('SELECT COUNT(*) as c FROM alert_reads WHERE user_id=? AND is_read=0', (user_id,)).fetchone()
        count = int(row['c'] or 0)
    return jsonify({'unread': count})


@app.route('/alerts/mark_all_read', methods=['POST'])
@login_required
def alerts_mark_all_read():
    user_id = session['user_id']
    with get_db() as conn:
        conn.execute('UPDATE alert_reads SET is_read=1 WHERE user_id=?', (user_id,))
        conn.commit()
    return jsonify({'ok': True})

# ============ RUN ============
if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    # enable debug/reload when FLASK_DEBUG=1 (default for local dev)
    debug = os.getenv('FLASK_DEBUG', '1') == '1'
    app.run(debug=debug, host='0.0.0.0', port=port, threaded=True)