import sqlite3
from datetime import datetime
db_path="databases/prices.db"
def init_db():
    with sqlite3.connect(db_path) as conn:
        cur=conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS price_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT,
                site TEXT,
                price TEXT,
                stock TEXT,
                discount TEXT,
                timestamp TEXT
                )
        """)
        conn.commit()
def insert_record(product,data):
    with sqlite3.connect(db_path) as conn:
        cur=conn.cursor()
        cur.execute("""
        INSERT INTO price_history  (product_name, site, price, stock, discount, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        product["name"],
        product["site"],
        data["price"],
        data["stock"],
        data["discount"],
        datetime.now().isoformat()
    ))
    conn.commit()


"""
with open(db_path):
    c=sqlite3.connect(db_path)
    print(c.execute("SELECT * FROM price_history").fetchall())
    """