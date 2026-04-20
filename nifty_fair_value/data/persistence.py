import sqlite3
import os
from datetime import datetime

# Resolve the database path relative to the project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.path.join(BASE_DIR, "nifty_engine.db")

def init_db():
    """Creates tables if they don't exist and purges stale records. Safe to call multiple times."""
    print(f"[DB] Initializing database at: {DB_NAME}")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Market Ticks Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ticks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        spot FLOAT,
        futures FLOAT,
        theoretical FLOAT,
        today_fair FLOAT,
        expiry_fair FLOAT,
        arbitrage FLOAT,
        ls_factor FLOAT,
        pcr FLOAT,
        max_pain FLOAT,
        atm_strike INTEGER,
        atm_ce_iv FLOAT,
        atm_pe_iv FLOAT
    )
    ''')
    
    # Signals Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        setup_type TEXT,
        signal_direction TEXT,
        entry FLOAT,
        sl FLOAT,
        target FLOAT,
        trailing_logic TEXT
    )
    ''')
    
    conn.commit()
    conn.close()
    
    # Automatically clean up data from previous days to keep the DB lean
    cleanup_old_data()

def cleanup_old_data():
    """Deletes records from previous days to keep the database performance optimal."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Delete ticks and signals older than today (local time)
        cursor.execute("DELETE FROM ticks WHERE date(timestamp, 'localtime') < date('now', 'localtime')")
        cursor.execute("DELETE FROM signals WHERE date(timestamp, 'localtime') < date('now', 'localtime')")
        
        conn.commit()
        deleted_count = cursor.execute("SELECT changes()").fetchone()[0]
        if deleted_count > 0:
            print(f"[DB] Purged {deleted_count} stale records from previous sessions.")
        conn.close()
    except Exception as e:
        print(f"Error during DB cleanup: {e}")

def save_market_tick(data):
    """Persists a single market data tick to the database."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO ticks (
            spot, futures, theoretical, today_fair, expiry_fair, 
            arbitrage, ls_factor, pcr, max_pain, atm_strike, 
            atm_ce_iv, atm_pe_iv
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('spot'),
            data.get('futures'),
            data.get('theoretical_price'),
            data.get('today_fair'),
            data.get('expiry_fair'),
            data.get('arbitrage'),
            data.get('ls_factor'),
            data.get('pcr_oi'),
            data.get('max_pain'),
            data.get('atm_strike'),
            data.get('atm_ce_iv'),
            data.get('atm_pe_iv')
        ))
        
        # Also log signal if active
        setup = data.get('setup', {})
        if setup and setup.get('type') != "No Active Setup":
            cursor.execute('''
            INSERT INTO signals (
                setup_type, signal_direction, entry, sl, target, trailing_logic
            ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                setup.get('type'),
                setup.get('signal'),
                setup.get('entry'),
                setup.get('sl'),
                setup.get('target'),
                setup.get('trailing')
            ))
            
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving to DB: {e}")

# Auto-initialize when module is imported — ensures tables exist
# regardless of whether init_db() was explicitly called by the caller.
init_db()


def get_history():
    """Fetches historical ticks for the current day."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Query records from the current day in local time, converting UTC to Local for display
        cursor.execute('''
        SELECT datetime(timestamp, 'localtime'), spot, today_fair, expiry_fair 
        FROM ticks 
        WHERE date(timestamp, 'localtime') = date('now', 'localtime')
        ORDER BY timestamp ASC
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        history = []
        for r in rows:
            # Extract HH:MM from timestamp
            ts_str = r[0]
            try:
                time_part = ts_str.split(' ')[1][:5]
            except (IndexError, AttributeError):
                time_part = ts_str
                
            history.append({
                "time": time_part,
                "spot": r[1],
                "today_fair": r[2],
                "expiry_fair": r[3]
            })
        return history
    except Exception as e:
        print(f"Error fetching history: {e}")
        return []
