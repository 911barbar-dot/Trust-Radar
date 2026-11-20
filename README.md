# Trust Radar

Findet alle 15 Minuten neue Coins, die in Trust Wallet sichtbar und handelbar werden, und schickt dir eine Telegram Nachricht, sobald einer den Durchbruch schafft.

**Quellen**
1. **Neu bei CoinMarketCap:** Trust Wallet bezieht seine Preisdaten von CoinMarketCap. Neue CMC Listings sind also die Coins, die gerade in Trust Wallet „ankommen“.
2. **Four.Meme Graduierungen:** Meme Coins auf BNB Chain, die ihre Bonding Curve gefüllt haben und auf PancakeSwap gewechselt sind. Diese sind direkt in Trust Wallet handelbar.

Jeder neue Coin wird 24 Stunden beobachtet. Schafft er in dieser Zeit die Schwellenwerte, bekommst du genau eine Nachricht.

**Durchbruch (Standard)**
- Marketcap mindestens 400k USD
- Liquidität mindestens 40k USD
- Volumen 24h mindestens 100k USD

Alles läuft kostenlos auf GitHub. Kein Server, kein PC, der laufen muss.

## Einrichtung (ca. 20 Minuten)

### 1. Telegram Bot erstellen
1. In Telegram **@BotFather** öffnen, `/newbot` senden, Namen vergeben.
2. Den **Token** kopieren (sieht aus wie `123456:ABC...`).
3. Deinem neuen Bot einmal „Hallo“ schreiben.
4. Im Browser öffnen: `https://api.telegram.org/bot<DEIN_TOKEN>/getUpdates`
   Dort steht bei `"chat":{"id":` deine **Chat ID** (eine Zahl).

### 2. Kostenlosen CoinMarketCap Schlüssel holen
1. Auf **pro.coinmarketcap.com/signup** gratis registrieren (Basic Plan, keine Kreditkarte).
2. Im Dashboard den **API Key** kopieren.

### 3. GitHub Repository anlegen
1. Auf github.com ein neues Repository erstellen (z. B. `trust-radar`, öffentlich).
2. Alle Dateien hochladen, inklusive der Ordner `.github` und `docs`.

### 4. Geheimnisse eintragen
Im Repository: **Settings > Secrets and variables > Actions > New repository secret**
- `TELEGRAM_BOT_TOKEN` = dein Bot Token
- `TELEGRAM_CHAT_ID` = deine Chat ID
- `CMC_API_KEY` = dein CoinMarketCap Schlüssel

### 5. Webseite aktivieren
**Settings > Pages**: Source „Deploy from a branch“, Branch `main`, Ordner `/docs`, speichern.
Die Seite ist danach unter `https://<dein-name>.github.io/trust-radar/` erreichbar.

### 6. Starten
**Actions > Trust Radar Scanner > Run workflow**. Der erste Lauf sammelt nur Daten, ohne Nachrichtenflut. Ab dem zweiten Lauf bekommst du Meldungen.

## Einstellungen anpassen
Unter **Settings > Secrets and variables > Actions > Variables**:

| Variable | Standard | Bedeutung |
|---|---|---|
| `MIN_MARKETCAP_USD` | 400000 | Mindest Marketcap |
| `MIN_LIQUIDITY_USD` | 40000 | Mindestliquidität |
| `MIN_VOLUME_24H_USD` | 100000 | Mindestvolumen 24h |
| `WATCH_HOURS` | 24 | Wie lange ein neuer Coin beobachtet wird |
| `SOURCES` | cmc,fourmeme | Nur eine Quelle: `cmc` oder `fourmeme` |

## Gut zu wissen
- Four.Meme Coins werden an ihrer Adresse erkannt (endet auf `4444`) und an einem neuen PancakeSwap Pool. Ändert Four.Meme dieses Muster, lässt es sich über die Variable `FOURMEME_SUFFIXES` anpassen.
- GitHub kann geplante Läufe bei hoher Auslastung einige Minuten verzögern.
- Ein Durchbruch ist kein Kaufsignal. Gerade nach Graduierungen verkaufen frühe Käufer oft. Keine Anlageberatung.
