from dataclasses import asdict
from confluent_kafka import Producer
from data_model import Listing
from datetime import datetime
from scrapling.fetchers import DynamicFetcher
import json
import schedule
import time


def delivery_report(err, msg):
    if err is not None:
        print(f'Message delivery failed: {err}')
    else:
        print(f'Message delivered to {msg.topic()} [{msg.partition()}] at offset {msg.offset()}')

def scrape_and_produce(url):
    p = Producer({'bootstrap.servers': 'localhost:9092'})
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scraping...")
    page = DynamicFetcher.fetch(url)
    cards = page.css('article.placard')
    print(f"Found {len(cards)} listings")

    for card in cards:
        try:
            price_raw = card.css('.priceTextBox span')[0].text
            beds_raw = card.css('.bedTextBox')[0].text
            first_image = card.css('.carousel-item img')[0].attrib['src']

            listing = Listing(
                listing_id = card.attrib['data-listingid'],
                url = card.attrib['data-url'],
                address = card.attrib['data-streetaddress'],
                price = int(price_raw.replace('$', '').replace(',', '').replace('+', '')),
                beds = int(beds_raw.split()[0]),
                baths = 1.0,
                pets_allowed = True,
                commute_miles = 0.0,
                scraped_at = datetime.now(),
                image_url = first_image)

            listing_dict = asdict(listing)
            listing_dict['scraped_at'] = listing_dict['scraped_at'].isoformat()
            message = json.dumps(listing_dict).encode('utf-8')
            p.produce('apartment-listings', value=message, callback=delivery_report)
        except (IndexError, KeyError, ValueError) as e:
            print(f"Skipping card: {e}")

    p.flush()

def start_schedule(url):
    schedule.every(15).minutes.do(scrape_and_produce, url)
    scrape_and_produce(url)
    while True:
        schedule.run_pending()
        time.sleep(1)
