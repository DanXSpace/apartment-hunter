from scrapling.fetchers import DynamicFetcher

page = DynamicFetcher.fetch('https://www.apartments.com/apartments-townhomes/min-2-bedrooms-1-bathrooms-under-2400-pet-friendly-dog/washer-dryer/?sk=5ee90fa3d016f89b63160accda2b8bb7&bb=lgjv8oz41H-oxhv41C&rt=4&mid=20260701')
cards = page.css('article.placard')

card = cards[0]
first_image = card.css('.carousel-inner img')[0].attrib['src']
# for card in cards:
#     url = card.attrib['data-url']
#     address = card.attrib['data-streetaddress']
#     price_raw = card.css('.priceTextBox span')[0].text
#     beds_raw = card.css('.bedTextBox')[0].text
#     baths = 1.0
#     commute_miles = 0.0
#     price = int(price_raw.replace('$', '').replace(',', '').replace('+', ''))
#     beds = int(beds_raw.split()[0])
#     print(price, beds, url, address)

# listing_url = cards[0].attrib['data-url']
# listing_page = DynamicFetcher.fetch(listing_url)
# print(listing_page.status)
# print(len(listing_page.html_content))

print(first_image)
