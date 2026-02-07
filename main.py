import json,csv
import time,os
import logging
from browser import get_page
import random
from parser import parse_page
from db import init_db, insert_record
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def load_products():
    with open("configs/prices.json", "r") as f:
        return json.load(f)

def scrape_product(product, page):   
    page.goto(product["url"], wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(random.randint(2000, 4000))
    html = page.content()
    data = parse_page(html, product["selectors"])
    return data if data['price']!='-' else None

def save_data(product, data):
    file_exists = os.path.isfile("Price_data.csv")
    with open("price_data.csv", "a", newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile,['Product Name','Site','Price','Stock','Discount','Timestamp'])
        if not file_exists: writer.writeheader()
        writer.writerow({'Product Name':product['name'],
                        'Site':product['site'],
                        'Price':data['price'],
                        'Stock': data['stock'],
                        'Discount': data['discount'],
                        'Timestamp': time.strftime("%Y-%m-%d %H:%M:%S")
                        })
    insert_record(product, data)        

def main():
    init_db()
    products = load_products()
    logging.basicConfig(
    filename="logs/price_scraper.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
    )
    logger = logging.getLogger()
    playwright, browser, context = get_page()
    for product in products:
        logger.info(f"Scraping {product['name']} from {product['site']}") 
        page = context.new_page()
        # Basic anti-bot delay
        time.sleep(random.uniform(2, 5))
        for attempt in range(2):
          try:
            data=scrape_product(product,page)
            break
          except PlaywrightTimeoutError as e:
            page.close() 
            logger.warning(f"Timeout error on attempt {attempt + 1} for {product['name']}: {e}")
            time.sleep(5)
          except Exception as e:
                logger.error(f"Error on attempt {attempt + 1} for {product['name']}: {e}")
        else:
            logger.error(f"Failed to scrape {product['name']} from {product['site']}")
        if data:
            save_data(product,data)
            logger.info("Data extracted")
    browser.close()
    playwright.stop()

if __name__ == "__main__":
    main()
    