
import sqlite3
import os
import time

db_path = r'C:\Users\akash\.gemini\antigravity\scratch\MindReader\instance\mind_reader.db'
if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
else:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Waiting for data to populate...")
    # Wait for at least one log to be written
    for _ in range(10):
        cursor.execute("SELECT COUNT(*) FROM eeg_log")
        count = cursor.fetchone()[0]
        if count > 0:
            break
        time.sleep(2)
        conn.commit()
    
    cursor.execute("SELECT * FROM eeg_log ORDER BY timestamp DESC LIMIT 5")
    rows = cursor.fetchall()
    
    # Get column names
    cursor.execute("PRAGMA table_info(eeg_log)")
    cols = [col[1] for col in cursor.fetchall()]
    print(f"Columns: {cols}")
    
    for row in rows:
        formatted_row = dict(zip(cols, row))
        print(f"--- Log {formatted_row['id']} ---")
        print(f"Mood: {formatted_row['mood']} | Engagement: {formatted_row['engagement']:.4f}")
        print(f"Valence: {formatted_row['valence']:.4f} | Arousal: {formatted_row['arousal']:.4f}")
        print(f"Coherence: {formatted_row.get('coherence', 0):.4f} | Cog Load: {formatted_row.get('cog_load', 0):.4f}")
    conn.close()
