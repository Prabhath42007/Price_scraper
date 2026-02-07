🛒 Multi-Site Price Tracker (Python + Playwright)

"A modular price monitoring system that scrapes product data from multiple e-commerce websites and stores results in both CSV and a database."

Designed to handle JavaScript-heavy sites using Playwright and structured so new products/sites can be added via configuration.

🚀 Features

>Scrapes prices from multiple websites

>Handles dynamic content (JS-rendered pages)

>Config-based product & selector management

>Detects basic CAPTCHA / blocked pages

>Saves data to:

    CSV file

    Database (via db.py)

>Logging system for tracking failures and retries

>Retry logic for timeouts

Project Structure:

price-tracker/
│
├── main.py                 # Main runner
├── browser.py              # Browser/context setup
├── parser.py               # HTML parsing logic
├── db.py                   # Database connection & insert
│
├── configs/
│   └── prices.json         # Product + selector config
│
├── logs/
│   └── price_scraper.log   # Logs
│
├── Price_data.csv          # Output CSV
└── README.md

Installation:

1️.Clone repository
git clone https://github.com/Prabhath42007/Price_scraper
cd price-tracker

2️.Create virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

3️.Install dependencies
pip install playwright
playwright install

Configuration:

Edit: configs/prices.json

Example product entry:

[
  {
    "name": "iPhone 15 128GB",
    "site": "Amazon",
    "url": "https://www.amazon.in/example-product",
    "selectors": {
      "price": "#priceblock_ourprice",
      "stock": "#availability span",
      "discount": ".savingsPercentage"
    }
  }
]

Selector Keys
Field	      Description
price	    Product price element
stock	    Availability text
discount	Discount % or amount

▶️ Running the Scraper: python main.py


Output will be saved in:

>CSV: Price_data.csv

>Database: via db.py

Logs available at logs/price_scraper.log

🔁 Retry & Error Handling

The scraper automatically retries when:

>A page times out

>Temporary loading failures occur

It logs:

>CAPTCHA blocks

>404 / product removed pages

Selector failures

Adding a New Product:

1.Open product page in browser

2.Inspect price / stock / discount elements

3.Add selectors to prices.json

4.Run scraper

5.Adjust selectors if logs show errors

Limitations:

>Some sites have strong bot protection

>Selectors break when website layout changes

>Not suitable for high-frequency scraping

Legal Disclaimer

Use responsibly. Always check website Terms of Service before scraping.

Future Improvements:

>Proxy rotation

>Headful mode with stealth

>Screenshot on error

>Scheduler (cron/Task Scheduler)

>Price change alerts (email/Telegram)