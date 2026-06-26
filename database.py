import sqlite3
import os
import sys

def get_db():
    # Prefer a database file next to the running executable (when frozen) or next to the source file.
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(base, 'reos.db')
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin',
        region TEXT,
        createdAt TEXT
    )
    ''')
    
    c.execute('''
    CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clientId INTEGER,
        clientName TEXT,
        phone TEXT,
        category TEXT,
        ptype TEXT,
        canonicalType TEXT,
        dtype TEXT,
        location TEXT,
        locationArea TEXT,
        locationCity TEXT,
        budget INTEGER,
        minBudget INTEGER,
        maxBudget INTEGER,
        config TEXT,
        minArea REAL,
        maxArea REAL,
        areaUnit TEXT,
        bhk INTEGER,
        spec TEXT,
        furnishing TEXT,
        powerRequirement TEXT,
        source TEXT,
        assignedTo TEXT,
        lastContact TEXT,
        nextAction TEXT,
        priority TEXT,
        status TEXT,
        notes TEXT,
        added TEXT,
        stageUpdatedAt TEXT
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        phone TEXT,
        email TEXT,
        budget INTEGER,
        req TEXT,
        location TEXT,
        locationArea TEXT,
        locationCity TEXT,
        interest TEXT,
        source TEXT,
        added TEXT
    )
    ''')
    
    c.execute('''
    CREATE TABLE IF NOT EXISTS properties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        cat TEXT,
        type TEXT,
        canonicalType TEXT,
        dtype TEXT,
        location TEXT,
        locationArea TEXT,
        locationCity TEXT,
        price INTEGER,
        area TEXT,
        areaValue REAL,
        areaUnit TEXT,
        bhk INTEGER,
        furnish TEXT,
        power TEXT,
        powerValue INTEGER,
        owner TEXT,
        ophone TEXT,
        desc TEXT,
        status TEXT,
        emoji TEXT,
        added TEXT,
        listedBy TEXT,
        brokerName TEXT,
        brokerPhone TEXT,
        parking TEXT,
        amenities TEXT,
        roadWidth TEXT,
        shedHeight TEXT
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS followups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clientId INTEGER,
        leadId INTEGER,
        region TEXT,
        clientName TEXT,
        action TEXT,
        priority TEXT,
        dueDate TEXT,
        dueTime TEXT,
        notes TEXT,
        done INTEGER,
        assignedTo TEXT,
        status TEXT,
        completedAt TEXT,
        completedBy TEXT,
        snoozedUntil TEXT,
        createdBy TEXT
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clientId INTEGER,
        leadId INTEGER,
        propertyId INTEGER,
        region TEXT,
        clientName TEXT,
        property TEXT,
        date TEXT,
        time TEXT,
        type TEXT,
        status TEXT,
        notes TEXT,
        outcome TEXT
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS shortlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clientId INTEGER,
        leadId INTEGER,
        propertyId INTEGER,
        region TEXT,
        clientName TEXT,
        propertyName TEXT,
        sent INTEGER,
        added TEXT
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clientId INTEGER,
        clientName TEXT,
        leadId INTEGER,
        propertyId INTEGER,
        region TEXT,
        kind TEXT,
        message TEXT,
        createdAt TEXT
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tableName TEXT NOT NULL,
        recordId INTEGER,
        action TEXT NOT NULL,
        username TEXT,
        message TEXT,
        createdAt TEXT
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS brokers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        phone TEXT,
        region TEXT,
        area TEXT,
        types TEXT,
        deals INTEGER,
        rating TEXT
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS regions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        createdAt TEXT
    )
    ''')

    for region in ['Ahmedabad Region', 'Vapi Region', 'Surat Region', 'Shared', 'Other']:
        c.execute("INSERT OR IGNORE INTO regions(name, createdAt) VALUES(?, datetime('now'))", (region,))

    ensure_column(c, 'leads', 'spec', 'TEXT')
    ensure_column(c, 'leads', 'clientId', 'INTEGER')
    ensure_column(c, 'leads', 'category', 'TEXT')
    ensure_column(c, 'leads', 'canonicalType', 'TEXT')
    ensure_column(c, 'leads', 'locationArea', 'TEXT')
    ensure_column(c, 'leads', 'locationCity', 'TEXT')
    ensure_column(c, 'leads', 'minBudget', 'INTEGER')
    ensure_column(c, 'leads', 'maxBudget', 'INTEGER')
    ensure_column(c, 'leads', 'minArea', 'REAL')
    ensure_column(c, 'leads', 'maxArea', 'REAL')
    ensure_column(c, 'leads', 'areaUnit', 'TEXT')
    ensure_column(c, 'leads', 'bhk', 'INTEGER')
    ensure_column(c, 'leads', 'furnishing', 'TEXT')
    ensure_column(c, 'leads', 'powerRequirement', 'TEXT')
    ensure_column(c, 'leads', 'source', 'TEXT')
    ensure_column(c, 'leads', 'assignedTo', 'TEXT')
    ensure_column(c, 'leads', 'lastContact', 'TEXT')
    ensure_column(c, 'leads', 'nextAction', 'TEXT')
    ensure_column(c, 'leads', 'stageUpdatedAt', 'TEXT')
    ensure_column(c, 'leads', 'region', 'TEXT')
    ensure_column(c, 'leads', 'createdBy', 'TEXT')
    ensure_column(c, 'users', 'region', 'TEXT')
    ensure_column(c, 'leads', 'closedBy', 'TEXT')
    ensure_column(c, 'leads', 'closedByType', 'TEXT')
    ensure_column(c, 'leads', 'closedPropertyId', 'INTEGER')
    ensure_column(c, 'leads', 'closedPropertyName', 'TEXT')
    ensure_column(c, 'leads', 'closedDate', 'TEXT')
    ensure_column(c, 'leads', 'closedValue', 'INTEGER')
    ensure_column(c, 'leads', 'closeReason', 'TEXT')
    ensure_column(c, 'properties', 'power', 'TEXT')
    ensure_column(c, 'properties', 'canonicalType', 'TEXT')
    ensure_column(c, 'properties', 'locationArea', 'TEXT')
    ensure_column(c, 'properties', 'locationCity', 'TEXT')
    ensure_column(c, 'properties', 'areaValue', 'REAL')
    ensure_column(c, 'properties', 'areaUnit', 'TEXT')
    ensure_column(c, 'properties', 'bhk', 'INTEGER')
    ensure_column(c, 'properties', 'powerValue', 'INTEGER')
    ensure_column(c, 'clients', 'source', 'TEXT')
    ensure_column(c, 'clients', 'locationArea', 'TEXT')
    ensure_column(c, 'clients', 'locationCity', 'TEXT')
    ensure_column(c, 'clients', 'region', 'TEXT')
    ensure_column(c, 'clients', 'createdBy', 'TEXT')
    ensure_column(c, 'properties', 'parking', 'TEXT')
    ensure_column(c, 'properties', 'amenities', 'TEXT')
    ensure_column(c, 'properties', 'roadWidth', 'TEXT')
    ensure_column(c, 'properties', 'shedHeight', 'TEXT')
    ensure_column(c, 'properties', 'region', 'TEXT')
    ensure_column(c, 'properties', 'createdBy', 'TEXT')
    ensure_column(c, 'meetings', 'outcome', 'TEXT')
    ensure_column(c, 'followups', 'clientId', 'INTEGER')
    ensure_column(c, 'followups', 'leadId', 'INTEGER')
    ensure_column(c, 'followups', 'region', 'TEXT')
    ensure_column(c, 'followups', 'assignedTo', 'TEXT')
    ensure_column(c, 'followups', 'status', 'TEXT')
    ensure_column(c, 'followups', 'completedAt', 'TEXT')
    ensure_column(c, 'followups', 'completedBy', 'TEXT')
    ensure_column(c, 'followups', 'snoozedUntil', 'TEXT')
    ensure_column(c, 'followups', 'createdBy', 'TEXT')
    ensure_column(c, 'brokers', 'region', 'TEXT')
    ensure_column(c, 'meetings', 'clientId', 'INTEGER')
    ensure_column(c, 'meetings', 'leadId', 'INTEGER')
    ensure_column(c, 'meetings', 'propertyId', 'INTEGER')
    ensure_column(c, 'meetings', 'region', 'TEXT')
    ensure_column(c, 'shortlist', 'clientId', 'INTEGER')
    ensure_column(c, 'shortlist', 'leadId', 'INTEGER')
    ensure_column(c, 'shortlist', 'propertyId', 'INTEGER')
    ensure_column(c, 'shortlist', 'region', 'TEXT')
    ensure_column(c, 'activities', 'clientId', 'INTEGER')
    ensure_column(c, 'activities', 'propertyId', 'INTEGER')
    ensure_column(c, 'activities', 'leadId', 'INTEGER')
    ensure_column(c, 'activities', 'region', 'TEXT')

    for table in ['leads', 'clients', 'properties', 'followups', 'meetings', 'shortlist', 'brokers']:
        ensure_column(c, table, 'deletedAt', 'TEXT')
        ensure_column(c, table, 'deletedBy', 'TEXT')

    ensure_column(c, 'activities', 'deletedAt', 'TEXT')
    ensure_column(c, 'activities', 'deletedBy', 'TEXT')

    conn.commit()
    conn.close()

def ensure_column(cursor, table, column, definition):
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

def query_db(query, args=(), one=False):
    conn = get_db()
    cur = conn.execute(query, args)
    rv = [dict((cur.description[i][0], value) for i, value in enumerate(row)) for row in cur.fetchall()]
    conn.close()
    return (rv[0] if rv else None) if one else rv

def insert_db(query, args=()):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, args)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id

def update_db(query, args=()):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, args)
    conn.commit()
    conn.close()
