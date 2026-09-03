# LE1252 Market Radar

Questo progetto sostituisce il vecchio controllo della pagina Fossil con un radar che cerca nuove informazioni sul Fossil Minecraft LE1252.

## Cosa fa

- ricerca ogni 30 minuti tramite GitHub Actions;
- cerca LE1252 e varianti del nome dell'orologio;
- cerca risultati web, eBay, Vinted, Reddit e forum;
- usa eBay Browse API quando sono configurati i relativi Secrets;
- salva gli elementi già visti in `seen_items.json`;
- invia Telegram solo per nuovi elementi;
- conserva uno storico giornaliero dei prezzi in `market_history.json`;
- invia un report giornaliero dopo le 18:00 ora italiana;
- non dichiara una vendita conclusa solo perché trova un prezzo online.

## Secrets

Obbligatori:

- `BOT_TOKEN`
- `CHAT_ID`

Opzionali:

- `EBAY_CLIENT_ID`
- `EBAY_CLIENT_SECRET`

Senza le credenziali eBay il radar continua a funzionare, ma le informazioni eBay dipenderanno dalla ricerca web.

## Installazione

1. Elimina dal vecchio repository `monitor.py`, `fossil_status.txt` e il vecchio `.github/workflows/main.yml`.
2. Copia i file di questa cartella nel repository.
3. Mantieni i Secrets `BOT_TOKEN` e `CHAT_ID` che hai già.
4. Se vuoi la ricerca eBay API, aggiungi `EBAY_CLIENT_ID` e `EBAY_CLIENT_SECRET` nei Secrets del repository.
5. Fai commit e push.
6. Vai in GitHub > Actions > LE1252 Market Radar.
7. Esegui manualmente `Run workflow` con `force_notify=true` per verificare Telegram.
8. Dopo il test puoi lasciarlo lavorare automaticamente.

## Importante

GitHub Actions non è un motore di ricerca web dedicato. La ricerca web usa il risultato HTML pubblico di DuckDuckGo e può quindi occasionalmente non restituire tutti i risultati. Per eBay, la Browse API è più affidabile quando configurata.

Vinted e altri marketplace possono non essere indicizzati immediatamente; il bot segnalerà ciò che riesce effettivamente a trovare.

`seen_items.json` va mantenuto: contiene la memoria delle segnalazioni già viste.
