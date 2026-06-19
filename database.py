import sqlite3
from datetime import datetime
from flask import g

DATABASE = 'instance/whatsapp.db'

def get_db():
    """Get database connection"""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    """Initialize database tables"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        
        # Users table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                google_id TEXT UNIQUE,
                email TEXT UNIQUE NOT NULL,
                name TEXT,
                picture TEXT,
                college_name TEXT,
                college_email TEXT,
                year INTEGER,
                is_admin INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Requests table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                need TEXT NOT NULL,
                issue TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                response TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                responded_at TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        
        # Create indexes for performance
        conn.execute('CREATE INDEX IF NOT EXISTS idx_requests_user_id ON requests(user_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)')
        
        conn.commit()

# ============ USER OPERATIONS ============

def create_user(google_id, email, name, picture, is_admin=False):
    """Create a new user"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            'INSERT INTO users (google_id, email, name, picture, is_admin) VALUES (?, ?, ?, ?, ?)',
            (google_id, email, name, picture, 1 if is_admin else 0)
        )
        conn.commit()
        return cur.lastrowid

def get_user_by_id(user_id):
    """Get user by ID"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

def get_user_by_google_id(google_id):
    """Get user by Google ID"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute('SELECT * FROM users WHERE google_id = ?', (google_id,)).fetchone()

def get_user_by_email(email):
    """Get user by email"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

def update_user_profile(user_id, college_name, college_email, year):
    """Update user profile"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            'UPDATE users SET college_name=?, college_email=?, year=? WHERE id=?',
            (college_name, college_email, year, user_id)
        )
        conn.commit()

def update_user_picture(user_id, picture):
    """Update user profile picture"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('UPDATE users SET picture = ? WHERE id = ?', (picture, user_id))
        conn.commit()

def make_admin(email):
    """Make a user admin"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('UPDATE users SET is_admin = 1 WHERE email = ?', (email,))
        conn.commit()

def get_all_users():
    """Get all users"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()

# ============ REQUEST OPERATIONS ============

def create_request(user_id, name, email, need, issue):
    """Create a new request"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            'INSERT INTO requests (user_id, name, email, need, issue) VALUES (?, ?, ?, ?, ?)',
            (user_id, name, email, need, issue)
        )
        conn.commit()
        return cur.lastrowid

def get_request_by_id(request_id):
    """Get request by ID"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute('SELECT * FROM requests WHERE id = ?', (request_id,)).fetchone()

def get_user_requests(user_id):
    """Get all requests for a user"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            'SELECT * FROM requests WHERE user_id = ? ORDER BY created_at DESC',
            (user_id,)
        ).fetchall()

def get_all_requests():
    """Get all requests (admin)"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute('SELECT * FROM requests ORDER BY created_at DESC').fetchall()

def get_pending_requests():
    """Get pending requests"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            'SELECT * FROM requests WHERE status = "pending" ORDER BY created_at DESC'
        ).fetchall()

def get_request_stats():
    """Get request statistics"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute('SELECT COUNT(*) as count FROM requests').fetchone()['count']
        pending = conn.execute('SELECT COUNT(*) as count FROM requests WHERE status="pending"').fetchone()['count']
        responded = conn.execute('SELECT COUNT(*) as count FROM requests WHERE status="responded"').fetchone()['count']
        resolved = conn.execute('SELECT COUNT(*) as count FROM requests WHERE status="resolved"').fetchone()['count']
        
        return {
            'total': total,
            'pending': pending,
            'responded': responded,
            'resolved': resolved
        }

def update_request_response(request_id, response):
    """Update request with admin response"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            'UPDATE requests SET response=?, status="responded", responded_at=CURRENT_TIMESTAMP WHERE id=?',
            (response, request_id)
        )
        conn.commit()

def update_request_status(request_id, status):
    """Update request status"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            'UPDATE requests SET status = ? WHERE id = ?',
            (status, request_id)
        )
        conn.commit()

def delete_request(request_id):
    """Delete a request"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('DELETE FROM requests WHERE id = ?', (request_id,))
        conn.commit()

def search_requests(query):
    """Search requests by keyword"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        search_term = f'%{query}%'
        return conn.execute(
            'SELECT * FROM requests WHERE name LIKE ? OR email LIKE ? OR need LIKE ? OR issue LIKE ? ORDER BY created_at DESC',
            (search_term, search_term, search_term, search_term)
        ).fetchall()

# ============ CLOSE CONNECTION ============

def close_db(error):
    """Close database connection"""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()