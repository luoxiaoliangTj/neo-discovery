import sqlite3

conn = sqlite3.connect('/home/lxl/src/neo_catalog.db')
c = conn.cursor()

print("=== NEO CATALOG DB ===")
c.execute('SELECT MAX(first_seen_date), COUNT(*) FROM neo_catalog')
row = c.fetchone()
print(f'Max date: {row[0]}, Total: {row[1]}')

c.execute('SELECT COUNT(*) FROM neo_catalog WHERE first_seen_date >= "2026-06-29"')
print(f'Added since 6/29: {c.fetchone()[0]}')

c.execute('SELECT COUNT(*) FROM neo_catalog WHERE first_seen_date >= "2026-06-30"')
print(f'Added since 6/30: {c.fetchone()[0]}')

conn.close()

print("\n=== TRACKER DB ===")
conn2 = sqlite3.connect('/home/lxl/src/neo_confirmation_tracker.db')
c2 = conn2.cursor()

c2.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = [r[0] for r in c2.fetchall()]
print(f'Tables: {tables}')

if 'candidate_tracking' in tables:
    c2.execute('SELECT COUNT(*) FROM candidate_tracking')
    print(f'Tracker total: {c2.fetchone()[0]}')
    try:
        c2.execute('SELECT MAX(first_seen_date) FROM candidate_tracking')
        print(f'Tracker max_date: {c2.fetchone()[0]}')
    except:
        print('No first_seen_date column in tracker')

conn2.close()
