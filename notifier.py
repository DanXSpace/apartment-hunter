from dotenv import load_dotenv
import os
from data_model import Listing
import requests

load_dotenv()
url = os.getenv('DISCORD_WEBHOOK_URL')

def send_alert(listing: Listing):
    pets_str = "Yes 🐾" if listing.pets_allowed else "No"
    commute_str = f"{listing.commute_miles:.1f} mi" if listing.commute_miles > 0 else "N/A"

    embed = {
        "title": f"🏠 {listing.address}",
        "url": listing.url,
        "description": "New listing matches your criteria!",
        "color": 5763719,  # green
        "fields": [
            {"name": "💰 Price", "value": f"**${listing.price:,}/mo**", "inline": True},
            {"name": "🛏 Beds", "value": str(listing.beds), "inline": True},
            {"name": "🛁 Baths", "value": str(listing.baths), "inline": True},
            {"name": "🐶 Pets Allowed", "value": pets_str, "inline": True},
            {"name": "🚗 Commute", "value": commute_str, "inline": True},
        ],
        "footer": {"text": f"Scraped at {listing.scraped_at.strftime('%b %d, %Y %I:%M %p')}"},
    }

    if listing.image_url:
        embed["image"] = {"url": listing.image_url}

    response = requests.post(url, json={"embeds": [embed]})

    if response.status_code == 204:
        print(f"Alert sent: {listing.address}")
    else:
        print(f"Failed to send alert. Status: {response.status_code}, Body: {response.text}")
