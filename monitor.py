import os
import requests
from bs4 import BeautifulSoup

URL = "https://www.fossil.com/it-it/products/cronografo-minecraft-x-fossil-the-end-44-mm-in-edizione-limitata/LE1252.html"

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

def send_telegram(message):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message}
    )

response = requests.get(URL, headers={
    "User-Agent": "Mozilla/5.0"
})

soup = BeautifulSoup(response.text, "html.parser")
text = soup.get_text(" ", strip=True)

if "Aggiungi al carrello" not in text:
    send_telegram(
        "🚨 FOSSIL THE END POTREBBE ESSERE SOLD OUT!\n\n"
        "Il pulsante 'Aggiungi al carrello' non è più presente.\n"
        "Controlla subito la pagina Fossil."
    )
