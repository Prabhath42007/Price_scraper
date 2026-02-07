from bs4 import BeautifulSoup
def parse_page(html,selectors):
    page=BeautifulSoup(html,'html.parser')
    def safe_select(selector):
        k=page.select_one(selector)
        return k.get_text(strip=True) if k else '-'
    return {
        "price":safe_select(selectors['price']),
        "stock":safe_select(selectors['stock']) ,
        "discount":safe_select(selectors['discount'])
    }


    

    

