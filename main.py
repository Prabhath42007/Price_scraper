import json,csv
import time,os
import logging
from browser import get_page
import random,sys
import pandas as pd
from db import init_db, insert_record
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from openpyxl.styles import Font, Alignment

def load_products():
    with open("configs/prices.json", "r") as f:
        return json.load(f)
    
def scrape_product(site,url,con,keys,page):   
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(random.randint(2000, 4000))
    page.wait_for_selector(con, timeout=30000)
    cards = page.locator(con)
    count = cards.count()
    results = []
    for i in range(count):
        if i>15:break
        card = cards.nth(i)
        item = {}
        for field, selector in keys.items():
            page.mouse.wheel(0, random.randint(10, 25))
            page.mouse.move(random.randint(100, 500), random.randint(100, 500))
            loc = card.locator(selector).first
            item[field] = loc.inner_text().strip() if loc.count() else ""
            page.mouse.wheel(0, random.randint(30, 75))
        time.sleep(random.uniform(1,4))
        item["Source"] = site
        item["Time stamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        results.append(item)
    return results

def save_data(data):
    file_exists = os.path.isfile("price_data.csv")
    with open("price_data.csv", "a", newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile,['Product Name','Source','Price','Offer','Rating','Delivery','Time stamp'])
        if not file_exists: writer.writeheader()
        for d in data:
            writer.writerow({
                'Product Name':d['Product Name'],
                'Source':d['Source'],
                'Price':d['Price'],
                'Offer':d['Offer'],
                'Rating':d['Rating'],
                'Delivery':d['Delivery'],
                'Time stamp':d['Time stamp']
            })       

def clean_data(data,output_file):
    df=pd.DataFrame(data)
    df = df.reindex(columns=['Product Name','Source','Price','Offer','Rating','Delivery','Time stamp'])
    df["Price"] = (
    df["Price"]
    .astype(str)
    .str.replace("₹", "", regex=False)   # Remove currency symbol
    .str.replace(",", "", regex=False)   # Remove commas
    .str.replace(" ", "", regex=False)   # Remove spaces
    )
    df["Price"] = pd.to_numeric(df["Price"], errors="coerce")
    df.drop_duplicates(inplace=True)
    df.dropna(how="all", inplace=True)
    df["Rating"] = df["Rating"].astype(str).str.extract(r"(\d+\.?\d*)")
    df["Rating"] = pd.to_numeric(df["Rating"], errors="coerce")
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Price Report")
        workbook = writer.book
        worksheet = writer.sheets["Price Report"]
        for cell in worksheet[1]:  # First row = headers
             cell.font = Font(bold=True)
             cell.alignment = Alignment(horizontal="center", vertical="center")       
        for column in worksheet.columns:
             max_length = 0
             column_letter = column[0].column_letter
             for cell in column:
                 try:
                     if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                 except:
                    pass
             worksheet.column_dimensions[column_letter].width = max_length + 2
        worksheet.auto_filter.ref = worksheet.dimensions
        worksheet.freeze_panes = "A2"
        for cell in worksheet["C"][1:]:  # Column C = Price (skip header)
             cell.number_format = '#,##0'

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
    res=[]
    try:
        if os.path.isfile("price_data.csv"):
            os.remove("price_data.csv")
            os.remove("Price_report.xlsx")
    except:pass
    for i in range(1,6):
        for site in products.keys():
          for name in products[site]['pages'].keys():
            for attempt in range(2):
                try:
                    page = context.new_page() 
                    page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
                        Object.defineProperty(navigator, 'languages', {get: () => ['en-IN', 'en']});
                    """)

                    data=scrape_product(site,products[site]['pages'][name]+str(i),products[site]['container'],products[site]['selectors'],page)
                    page.close()
                    for item in data:
                        res.append(item)
                    break
                except PlaywrightTimeoutError as e:
                    logger.warning(f"Timeout error on attempt {attempt + 1} for {name}-{i}: {e}")
                except Exception as e:
                        logger.error(f"Error on attempt {attempt + 1} for {name}-{i}: {e}")
                        break
                page.close() 
                time.sleep(5)
            else:
                logger.error(f"Failed to scrape {name}-{i} from {site}")
                continue
            logger.info(f"Data extracted for {name}-{i} from {site}")
            time.sleep(random.uniform(2, 5))
    browser.close()
    playwright.stop()
    if res:
        save_data(res)
        clean_data(res, "Price_report.xlsx")
        logger.info(f"Scraped {len(res)} products")
    else:
        logger.error("No data scraped")

if __name__ == "__main__":
    main()

    
