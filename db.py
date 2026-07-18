import psycopg2
from psycopg2.extras import execute_values
from rapidfuzz import fuzz


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def save_to_db(keyword: str, data: list[dict]) -> None:
    """Insert cleaned records into price_history."""
    if not data:
        return
    con = get_conn()
    cur = con.cursor()
 
    rows = []
    for row in data:
        try:
            price  = float(row["Price"])  if row.get("Price")  else None
            rating = float(row["Rating"]) if row.get("Rating") else None
            alert=row["Drop Alert"] if row.get("Drop Alert") else None
            pct=row["Drop %"] if row.get("Drop %") else None
        except (ValueError, TypeError):
            price  = None
            rating = None
 
        rows.append((
            keyword,
            row.get("Product Name"),
            price,
            rating,
            row.get("Source"),
            alert,
            pct,
            row.get("Time stamp"),
        ))
 
    execute_values(
        cur,
        """INSERT INTO price_history
           (keyword, name, price, rating, source, drop_alert,drop_pct, scraped_at)
           VALUES %s""",
        rows,
    )
    con.commit()
    cur.close()
    con.close()
 
 
def detect_drops(keyword: str, new_data: list[dict]) -> list[dict]:
    """
    Compare each item's price against the last known price for a
    fuzzy-matched product name in the DB.
 
    Expects new_data prices to already be cleaned floats.
    """
    con = get_conn()
    cur = con.cursor()
 
    cur.execute(
        """SELECT name, price
           FROM price_history
           WHERE keyword = %s
           ORDER BY scraped_at DESC
           LIMIT 200""",
        (keyword,),                 
    )
    history = cur.fetchall()         # list of (name, price) from DB
    cur.close()
    con.close()
 
    for item in new_data:
        new_price = item.get("Price")
 
        # Skip items where price cleaning failed
        if not isinstance(new_price, (int, float)):
            item["Prev Price"] = ""
            item["Drop %"]     = ""
            item["Drop Alert"] = "No price"
            continue
 
        best_match_price = None
        best_score       = 0
 
        for db_name, db_price in history:
            if not db_name or db_price is None:
                continue
            score = fuzz.token_sort_ratio(
                item["Product Name"].lower(),
                db_name.lower()
            )
            if score > best_score:
                best_score       = score
                best_match_price = float(db_price)
 
        if best_match_price is not None and best_score >= 80:
            drop_amt = best_match_price - new_price
            drop_pct = (drop_amt / best_match_price) * 100
 
            item["Prev Price"] = best_match_price
            item["Drop %"]     = round(drop_pct, 2)
 
            if drop_pct >= 10:
                item["Drop Alert"] = "URGENT"
            elif drop_pct >= 5:
                item["Drop Alert"] = "YES"
            elif drop_pct < 0:
                item["Drop %"]     = round(abs(drop_pct), 2)
                item["Drop Alert"] = "PRICE UP"   # competitor raised price
            else:
                item["Drop %"]     = None
                item["Drop Alert"] = ""
        else:
            item["Prev Price"] = ""
            item["Drop %"]     = ""
            item["Drop Alert"] = "First scan"
 
    return new_data
