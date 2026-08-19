import sqlite3
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

DB_FILE = "users_db.sqlite"
FREE_LIMIT = 5  # The search limit for free users

def get_db_connection():
    """Establishes and returns a SQLite connection."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """Initializes the database, creates the users and marketing tables, and migrates schemas."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Create users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            search_count INTEGER DEFAULT 0,
            created_at TIMESTAMP
        )
    """)
    conn.commit()
    
    # 2. Add subscription_status column if it doesn't exist
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN subscription_status TEXT DEFAULT 'free'")
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists
        pass
        
    # 3. Create marketing campaign table to track cold emails
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS marketing_campaign (
            email TEXT PRIMARY KEY,
            company_name TEXT,
            website TEXT,
            status TEXT DEFAULT 'scraped',
            sent_at TIMESTAMP
        )
    """)
    conn.commit()
    
    conn.close()

def get_search_count(user_id: int) -> int:
    """Returns the current search count for the user. Returns 0 if not registered."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT search_count FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row["search_count"]
    return 0

def increment_search_count(user_id: int, username: str) -> int:
    """Increments the search count for a user. Registers the user if they don't exist.
    Returns the new search count.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if user exists
    cursor.execute("SELECT search_count FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if row:
        # Increment
        new_count = row["search_count"] + 1
        cursor.execute(
            "UPDATE users SET search_count = ?, username = ? WHERE user_id = ?",
            (new_count, username, user_id)
        )
    else:
        # Register and set count to 1
        new_count = 1
        cursor.execute(
            "INSERT INTO users (user_id, username, search_count, created_at) VALUES (?, ?, ?, ?)",
            (user_id, username, new_count, datetime.now().isoformat())
        )
        
    conn.commit()
    conn.close()
    return new_count

def get_subscription_status(user_id: int) -> str:
    """Returns the user's subscription status: 'free' or 'premium'."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT subscription_status FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row["subscription_status"]:
        return row["subscription_status"]
    return "free"

def set_subscription_status(user_id: int, status: str) -> None:
    """Updates the user's subscription status (e.g. to 'premium' or 'free')."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET subscription_status = ? WHERE user_id = ?",
        (status, user_id)
    )
    conn.commit()
    conn.close()

# Marketing campaign queries
def add_marketing_lead(email: str, company_name: str, website: str, status: str = "scraped") -> None:
    """Saves a new unique harvested B2B lead to the database campaign list."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO marketing_campaign (email, company_name, website, status) VALUES (?, ?, ?, ?)",
            (email, company_name, website, status)
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Error adding marketing lead: {e}")
    finally:
        conn.close()

def get_unsent_marketing_leads(limit: int = 5) -> list:
    """Returns a list of harvested B2B leads that have not received an email yet."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT email, company_name, website FROM marketing_campaign WHERE status = 'scraped' LIMIT ?",
        (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def mark_lead_as_sent(email: str, status: str = "sent") -> None:
    """Updates the campaign status and records the email timestamp."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE marketing_campaign SET status = ?, sent_at = ? WHERE email = ?",
        (status, datetime.now().isoformat(), email)
    )
    conn.commit()
    conn.close()

def get_marketing_campaign_stats() -> dict:
    """Returns statistics of the marketing outreach campaign."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM marketing_campaign")
    total = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM marketing_campaign WHERE status = 'scraped'")
    scraped = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM marketing_campaign WHERE status = 'sent'")
    sent = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM marketing_campaign WHERE status = 'failed'")
    failed = cursor.fetchone()[0]
    
    conn.close()
    return {"total": total, "scraped": scraped, "sent": sent, "failed": failed}

def get_recent_sent_leads(limit: int = 5) -> list:
    """Returns the last few business leads that successfully received an email."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT email, company_name, sent_at FROM marketing_campaign WHERE status = 'sent' ORDER BY sent_at DESC LIMIT ?",
        (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
