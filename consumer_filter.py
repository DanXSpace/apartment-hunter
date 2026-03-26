from data_model import Listing
from confluent_kafka import Consumer
import json
import time
from datetime import datetime
from notifier import send_alert

def check_file():
    try:
        with open('seen_listings.txt', 'r', encoding='utf-8') as f:
            content = f.read()
            split_ids = set(content.split("\n")) - {""}
    except FileNotFoundError:
        print("File not found")
        split_ids = set()
    return split_ids

def start_consumer():
    c = Consumer({
        'bootstrap.servers': 'localhost:9092',
        'group.id': 'mygroup3',
        'auto.offset.reset': 'earliest'
    })
    c.subscribe(['apartment-listings'])
    deduped_listings = check_file()
    while True:
        msg = c.poll(1.0)
        if msg:
            decoded_msg = msg.value().decode('utf-8')
            json_msg = json.loads(decoded_msg)
            json_msg['scraped_at'] = datetime.fromisoformat(json_msg['scraped_at'])
            listing = Listing(**json_msg)
            if listing.listing_id not in deduped_listings:
                deduped_listings.add(listing.listing_id)
                send_alert(listing)
                time.sleep(1)
                with open("seen_listings.txt", "w") as f:
                    f.write("\n".join(deduped_listings))
