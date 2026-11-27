"""
Trust Radar Scanner
Findet neue Coins, die in Trust Wallet sichtbar und handelbar werden:
  1. Neu bei CoinMarketCap gelistet (Trust Wallet bezieht seine Preise von CMC)
  2. Graduierte Meme Coins: Pump.fun auf PumpSwap (Solana) und BNB Meme Coins auf PancakeSwap
  3. Neue Coin Profile und Boosts bei DexScreener (Solana und BNB)
Jeder Kandidat wird 24 Stunden beobachtet. Sobald er die Schwellenwerte erreicht,
kommt eine Telegram Nachricht.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
DATA_FILE = ROOT / "docs" / "data.json"

# ---------- Einstellungen (in GitHub unter Settings > Secrets and variables > Actions > Variables) ----------
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY_USD", "40000"))
MIN_VOLUME_24H = float(os.getenv("MIN_VOLUME_24H_USD", "100000"))
MIN_MARKETCAP = float(os.getenv("MIN_MARKETCAP_USD", "400000"))
WATCH_HOURS = float(os.getenv("WATCH_HOURS", "24"))
SOURCES = [s.strip() for s in os.getenv("SOURCES", "cmc,fourmeme").split(",") if s.strip()]
FOURMEME_SUFFIXES = [s.strip().lower() for s in os.getenv("FOURMEME_SUFFIXES", "4444").split(",") if s.strip()]
GT_PAGES = int(os.getenv("GECKOTERMINAL_PAGES", "10"))
MAX_PAIR_AGE_HOURS = float(os.getenv("MAX_PAIR_AGE_HOURS", "72"))  # Meme Coins: nur wirklich neue Coins melden

CMC_KEY = os.getenv("CMC_API_KEY", "")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

UA = {"User-Agent": "trust-radar/1.0", "Accept": "application/json"}
MAX_ENTRIES = 300
STATS = {}

CHAIN_LABEL = {
    "ethereum": "Ethereum", "bsc": "BNB Chain", "solana": "Solana", "base": "Base",
    "polygon": "Polygon", "arbitrum": "Arbitrum", "optimism": "Optimism",
    "avalanche": "Avalanche", "tron": "Tron", "ton": "TON", "sui": "Sui",
}
# CoinMarketCap Plattform > DexScreener chainId (Reihenfolge wichtig: spezifisch vor allgemein)
CMC_PLATFORM = [
    ("bnb", "bsc"), ("bep20", "bsc"), ("binance", "bsc"), ("arbitrum", "arbitrum"),
    ("optimism", "optimism"), ("base", "base"), ("solana", "solana"), ("polygon", "polygon"),
    ("avalanche", "avalanche"), ("tron", "tron"), ("toncoin", "ton"), ("ton", "ton"),
    ("sui", "sui"), ("ethereum", "ethereum"),
]


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get(url, **kw):
    kw.setdefault("timeout", 25)
    kw["headers"] = {**UA, **kw.get("headers", {})}
    for attempt in range(3):
        try:
            r = requests.get(url, **kw)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            return r
        except requests.RequestException as e:
            print("Netzwerkfehler:", e)
            time.sleep(2)
    return None


# ---------- Quelle 1: CoinMarketCap ----------

def cmc_chain(platform):
    if not platform:
        return None
    text = f"{platform.get('slug', '')} {platform.get('name', '')}".lower()
    for needle, chain in CMC_PLATFORM:
        if needle in text:
            return chain
    return None


def source_cmc():
    if CMC_KEY:
        base, headers = "https://pro-api.coinmarketcap.com", {"X-CMC_PRO_API_KEY": CMC_KEY}
    else:
        base, headers = "https://pro-api.coinmarketcap.com/trial-pro-api", {}
    # Neueste Listings nach Aufnahmedatum (im kostenlosen Basic Plan enthalten)
    r = get(f"{base}/v1/cryptocurrency/listings/latest",
            params={"limit": 100, "convert": "USD", "sort": "date_added", "sort_dir": "desc"},
            headers=headers)
    if not r or r.status_code != 200:
        print("CoinMarketCap nicht erreichbar:", r.status_code if r else "keine Antwort")
        STATS["cmc_error"] = r.status_code if r else "keine Antwort"
        return []
    out = []
    for c in r.json().get("data", []):
        plat = c.get("platform") or {}
        chain = cmc_chain(plat)
        addr = plat.get("token_address")
        if not (chain and addr):
            continue  # eigene Blockchains ohne Token Adresse überspringen
        out.append({
            "source": "CoinMarketCap",
            "chain": chain,
            "address": addr,
            "name": c.get("name", "?"),
            "symbol": c.get("symbol", "?"),
            "listed_at": c.get("date_added") or datetime.now(timezone.utc).isoformat(),
            "source_url": f"https://coinmarketcap.com/currencies/{c.get('slug', '')}/",
        })
    STATS["cmc_found"] = len(out)
    print(f"CoinMarketCap: {len(out)} neue Listings")
    return out


# ---------- Quelle 2: Meme Coins auf Solana (Pump.fun) und BNB Chain ----------
# Pump.fun Coins wechseln nach dem Graduieren auf PumpSwap, BNB Meme Coins (z. B. Four.Meme) auf PancakeSwap.
# Über GeckoTerminal holen wir die jüngsten Pools dieser Börsen, sortiert nach Volumen, plus Trending und Neueste.
# So gehen auch bei stündlichen Läufen keine starken Coins verloren.

GT = "https://api.geckoterminal.com/api/v2/networks"
MEME_NETWORKS = {
    # Netzwerk: (DexScreener chainId, Suchwort für die Börse, Ersatz Börsen IDs, Quellenname)
    "solana": ("solana", "pumpswap", ["pumpswap"], "Pump.fun"),
    "bsc": ("bsc", "pancakeswap", ["pancakeswap_v2", "pancakeswap-v3-bsc"], "BNB Meme"),
}
QUOTE_TOKENS = {
    "so11111111111111111111111111111111111111112", "epjfwdd5aufqssqem2qn1xzybapc8g4wegggkzwytdt1v",
    "es9vmfrzacermjfrf4h2fyd4kcomky11mcce8benwnyb",
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "0x55d398326f99059ff775485246999027b3197955",
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", "0xe9e7cea3dedca5984780bafc599bd69add087d56",
    "0xc5f0f7b66764f6ec8c8dff7ba683102295e16409", "0x2170ed0880ac9a755fd29b2688956bd959f933f8",
    "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",
}


def _parse_pools(body, cutoff, dex_ids, network, label):
    tokens = {t["id"]: t["attributes"] for t in body.get("included", []) if t.get("type") == "token"}
    out = []
    for p in body.get("data", []):
        rel = p.get("relationships", {})
        dex = (rel.get("dex", {}).get("data") or {}).get("id", "")
        if dex not in dex_ids:
            continue
        created = p.get("attributes", {}).get("pool_created_at")
        if created:
            try:
                if datetime.fromisoformat(created.replace("Z", "+00:00")) < cutoff:
                    continue
            except ValueError:
                pass
        for side in ("base_token", "quote_token"):
            tok_id = (rel.get(side, {}).get("data") or {}).get("id", "")
            tok = tokens.get(tok_id, {})
            addr = tok.get("address") or tok_id.split("_", 1)[-1]
            if not addr or addr.lower() in QUOTE_TOKENS:
                continue
            chain = MEME_NETWORKS[network][0]
            out.append({
                "source": label,
                "chain": chain,
                "address": addr,
                "name": tok.get("name") or p["attributes"].get("name", "?").split(" / ")[0],
                "symbol": tok.get("symbol") or p["attributes"].get("name", "?").split(" / ")[0],
                "listed_at": created or datetime.now(timezone.utc).isoformat(),
                "source_url": (f"https://pump.fun/coin/{addr}" if network == "solana"
                               else f"https://dexscreener.com/{chain}/{addr}"),
            })
            break
    return out


def source_memes():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WATCH_HOURS)
    inc = {"include": "base_token,quote_token"}
    done, errors, found = 0, 0, {}

    def fetch(url, params):
        nonlocal done, errors
        r = get(url, params=params)
        done += 1
        time.sleep(2.2)  # GeckoTerminal erlaubt 30 Abfragen pro Minute
        if not r or r.status_code != 200:
            errors += 1
            print("GeckoTerminal Fehler:", url, r.status_code if r else "keine Antwort")
            return None
        return r.json()

    for network, (_, keyword, fallback, label) in MEME_NETWORKS.items():
        dex_ids = []
        for page in range(1, 4):
            body = fetch(f"{GT}/{network}/dexes", {"page": page})
            if not body or not body.get("data"):
                break
            dex_ids += [d["id"] for d in body["data"] if keyword in d.get("id", "")]
            if dex_ids:
                break
        dex_ids = dex_ids or fallback
        print(f"{label} Börsen:", dex_ids)

        jobs = []
        for dex in dex_ids[:2]:
            for page in range(1, 4):
                jobs.append((f"{GT}/{network}/dexes/{dex}/pools",
                             {**inc, "page": page, "sort": "h24_volume_usd_desc"}))
        for dur in ("1h", "6h"):
            jobs.append((f"{GT}/{network}/trending_pools", {**inc, "page": 1, "duration": dur}))
        for page in range(1, 4):
            jobs.append((f"{GT}/{network}/new_pools", {**inc, "page": page}))

        n_before = len(found)
        for url, params in jobs:
            body = fetch(url, params)
            if body:
                for c in _parse_pools(body, cutoff, set(dex_ids), network, label):
                    found.setdefault(f"{c['chain']}:{c['address'].lower()}", c)
        STATS[f"{label}_found"] = len(found) - n_before

    STATS["gecko_requests"] = done
    STATS["gecko_errors"] = errors
    print(f"Meme Coins: {len(found)} Kandidaten ({done} Abfragen, {errors} Fehler)")
    return list(found.values())


# ---------- Quelle 3: DexScreener Profile und Boosts (Solana und BNB) ----------

def source_dexscreener():
    out, seen = [], set()
    for path in ("token-profiles/latest/v1", "token-boosts/latest/v1", "token-boosts/top/v1"):
        r = get(f"https://api.dexscreener.com/{path}")
        if not r or r.status_code != 200:
            print("DexScreener Fehler:", path)
            continue
        items = r.json() if isinstance(r.json(), list) else []
        for it in items:
            chain, addr = it.get("chainId"), it.get("tokenAddress")
            if chain not in ("solana", "bsc") or not addr or (chain, addr.lower()) in seen:
                continue
            seen.add((chain, addr.lower()))
            out.append({
                "source": "DexScreener",
                "chain": chain,
                "address": addr,
                "name": "?",
                "symbol": "?",
                "listed_at": datetime.now(timezone.utc).isoformat(),
                "source_url": it.get("url") or f"https://dexscreener.com/{chain}/{addr}",
            })
        time.sleep(0.5)
    STATS["DexScreener_found"] = len(out)
    print(f"DexScreener: {len(out)} Kandidaten")
    return out


# ---------- Marktdaten (DexScreener, bis 30 Adressen pro Abfrage) ----------

def fetch_markets(chain, addresses):
    result = {}
    for i in range(0, len(addresses), 30):
        batch = addresses[i:i + 30]
        r = get(f"https://api.dexscreener.com/tokens/v1/{chain}/{','.join(batch)}")
        if not r or r.status_code != 200:
            continue
        pairs = r.json() if isinstance(r.json(), list) else r.json().get("pairs") or []
        by_token = {}
        for p in pairs:
            key = (p.get("baseToken") or {}).get("address", "").lower()
            by_token.setdefault(key, []).append(p)
        for addr in batch:
            ps = by_token.get(addr.lower())
            if not ps:
                continue
            best = max(ps, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
            tx = (best.get("txns") or {}).get("h24") or {}
            result[addr.lower()] = {
                "price_usd": float(best.get("priceUsd") or 0),
                "liquidity_usd": sum(((p.get("liquidity") or {}).get("usd") or 0) for p in ps),
                "volume_24h_usd": sum(((p.get("volume") or {}).get("h24") or 0) for p in ps),
                "marketcap_usd": best.get("marketCap") or best.get("fdv") or 0,
                "change_24h": (best.get("priceChange") or {}).get("h24") or 0,
                "buys_24h": tx.get("buys", 0),
                "sells_24h": tx.get("sells", 0),
                "dex_url": best.get("url"),
                "pair_age_hours": round((time.time() * 1000 - min(p.get("pairCreatedAt") or time.time() * 1000 for p in ps)) / 3_600_000, 1),
                "token_name": (best.get("baseToken") or {}).get("name"),
                "token_symbol": (best.get("baseToken") or {}).get("symbol"),
            }
        time.sleep(0.5)
    return result


# ---------- Bewertung ----------

def score(m):
    """0 bis 100 Punkte aus Liquidität, Volumen, Marketcap, Kaufdruck und Momentum."""
    if not m:
        return 0, ["Noch keine Handelsdaten"]
    pts, notes = 0, []

    def tier(value, levels, max_pts):
        hits = sum(1 for lvl in levels if value >= lvl)
        return max_pts * hits / len(levels)

    pts += tier(m["liquidity_usd"], [25_000, 100_000, 500_000, 2_000_000], 30)
    pts += tier(m["volume_24h_usd"], [50_000, 250_000, 1_000_000, 5_000_000], 25)
    pts += tier(m["marketcap_usd"], [250_000, 1_000_000, 10_000_000, 50_000_000], 20)

    total = m["buys_24h"] + m["sells_24h"]
    if total >= 100:
        ratio = m["buys_24h"] / total
        pts += 15 * min(max((ratio - 0.4) / 0.3, 0), 1)
        if ratio > 0.55:
            notes.append(f"Kaufdruck {ratio:.0%}")
    if m["change_24h"] > 0:
        pts += min(m["change_24h"] / 10, 10)

    if m["liquidity_usd"] and m["volume_24h_usd"] / m["liquidity_usd"] > 20:
        notes.append("Volumen sehr hoch im Verhältnis zur Liquidität, Vorsicht")
    if m["change_24h"] < -40:
        notes.append("Starker Preiseinbruch")
    if 0 < m["liquidity_usd"] < 25_000:
        notes.append("Wenig Liquidität")
    return round(min(pts, 100)), notes


def is_breakthrough(m):
    return bool(m) and (
        m["liquidity_usd"] >= MIN_LIQUIDITY
        and m["volume_24h_usd"] >= MIN_VOLUME_24H
        and m["marketcap_usd"] >= MIN_MARKETCAP
    )


# ---------- Telegram ----------

def usd(v):
    if v >= 1_000_000_000:
        return f"${v/1_000_000_000:.2f} Mrd."
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f} Mio."
    if v >= 1_000:
        return f"${v/1_000:.1f}k"
    return f"${v:,.2f}"


def tg_send(text):
    if not (TG_TOKEN and TG_CHAT):
        print("Telegram nicht eingerichtet")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        if r.status_code != 200:
            print("Telegram Fehler:", r.status_code, r.text[:200])
        return r.status_code == 200
    except requests.RequestException as ex:
        print("Telegram Fehler:", ex)
        return False


def status_text(title, watch, data):
    hot = sum(1 for t in data["tokens"] if t.get("breakthrough"))
    lines = [f"<b>{title}</b>",
             f"Coins in Beobachtung: {len(watch)}",
             f"Durchbrüche gesamt: {hot}",
             f"CoinMarketCap: {STATS.get('cmc_found', 0)} Kandidaten"
             + (f" (Fehler {STATS['cmc_error']})" if "cmc_error" in STATS else ""),
             f"Pump.fun (Solana): {STATS.get('Pump.fun_found', 0)} Kandidaten",
             f"BNB Meme: {STATS.get('BNB Meme_found', 0)} Kandidaten",
             f"DexScreener: {STATS.get('DexScreener_found', 0)} Kandidaten"]
    if STATS.get("gecko_errors"):
        lines.append(f"⚠️ GeckoTerminal: {STATS['gecko_errors']} Fehler")
    return "\n".join(lines)


def send_telegram(e):
    if not (TG_TOKEN and TG_CHAT):
        print("Telegram nicht eingerichtet. Treffer:", e["symbol"])
        return
    m = e["market"] or {}
    lines = [
        f"🚀 <b>Durchbruch: {e['name']} ({e['symbol']})</b>",
        f"Quelle: {e['source']} | {e['chain_label']}",
        f"Score: <b>{e['score']}/100</b>",
        f"Marketcap: {usd(m.get('marketcap_usd', 0))}",
        f"Liquidität: {usd(m.get('liquidity_usd', 0))}",
        f"Volumen 24h: {usd(m.get('volume_24h_usd', 0))}",
        f"Preis 24h: {m.get('change_24h', 0):+.1f}%",
    ]
    if e["notes"]:
        lines.append("Hinweise: " + ", ".join(e["notes"]))
    lines.append(f"\n<code>{e['address']}</code>")
    links = []
    if m.get("dex_url"):
        links.append(f'<a href="{m["dex_url"]}">Chart</a>')
    links.append(f'<a href="{e["source_url"]}">{e["source"]}</a>')
    lines.append(" | ".join(links))
    lines.append("\n<i>Keine Anlageberatung. Immer selbst prüfen.</i>")
    tg_send("\n".join(lines))


# ---------- Ablauf ----------

def main():
    state = load_json(STATE_FILE, {})
    data = load_json(DATA_FILE, {"updated": None, "tokens": []})
    first_run = "last_run" not in state
    now = datetime.now(timezone.utc)
    watch = state.get("watch", {})  # key > {first_seen, alerted}

    # 1. Neue Kandidaten sammeln
    candidates = []
    if "cmc" in SOURCES:
        candidates += source_cmc()
    if "fourmeme" in SOURCES or "memes" in SOURCES:
        candidates += source_memes()
    if "fourmeme" in SOURCES or "dexscreener" in SOURCES:
        candidates += source_dexscreener()

    entries = {f"{t['chain']}:{t['address'].lower()}": t for t in data["tokens"]}
    for c in candidates:
        key = f"{c['chain']}:{c['address'].lower()}"
        if key in entries:
            continue
        entries[key] = {**c, "chain_label": CHAIN_LABEL.get(c["chain"], c["chain"]),
                        "market": None, "score": 0, "notes": [], "breakthrough": False}
        watch.setdefault(key, {"first_seen": now.isoformat(), "alerted": False})

    # 2. Beobachtungsliste aufräumen (nur die letzten X Stunden)
    cutoff = now - timedelta(hours=WATCH_HOURS)
    watch = {k: v for k, v in watch.items()
             if datetime.fromisoformat(v["first_seen"]) > cutoff and k in entries}

    # 3. Marktdaten für alle beobachteten Coins aktualisieren
    by_chain = {}
    for key in watch:
        chain, _ = key.split(":", 1)
        by_chain.setdefault(chain, []).append(entries[key]["address"])
    alerts = 0
    for chain, addrs in by_chain.items():
        markets = fetch_markets(chain, addrs)
        for addr in addrs:
            key = f"{chain}:{addr.lower()}"
            e = entries[key]
            e["market"] = markets.get(addr.lower()) or e.get("market")
            m = e["market"] or {}
            if e.get("name") in (None, "?") and m.get("token_name"):
                e["name"], e["symbol"] = m["token_name"], m.get("token_symbol") or "?"
            e["score"], e["notes"] = score(e["market"])
            too_old = (e["source"] != "CoinMarketCap" and m.get("pair_age_hours") is not None
                       and m["pair_age_hours"] > MAX_PAIR_AGE_HOURS)
            hit = is_breakthrough(e["market"]) and not too_old
            e["breakthrough"] = e["breakthrough"] or hit
            if hit and not watch[key]["alerted"]:
                watch[key]["alerted"] = True
                e["breakthrough_at"] = now.isoformat()
                if not first_run:  # beim allerersten Lauf keine Nachrichtenflut
                    send_telegram(e)
                    alerts += 1

    data["tokens"] = sorted(entries.values(), key=lambda t: t["listed_at"], reverse=True)[:MAX_ENTRIES]
    data["updated"] = now.isoformat()
    data["settings"] = {"min_liquidity": MIN_LIQUIDITY, "min_volume": MIN_VOLUME_24H,
                        "min_marketcap": MIN_MARKETCAP, "watch_hours": WATCH_HOURS}
    # Testnachricht nach dem Update und tägliche Statusmeldung ab 08:00 Uhr Schweizer Zeit
    local = now.astimezone(ZoneInfo("Europe/Zurich"))
    if state.get("version") != 3:  # einmalige Bestätigung nach diesem Update
        state["version"] = 3
        state["telegram_verified"] = False
    if not state.get("telegram_verified"):
        if tg_send(status_text("✅ Trust Radar läuft (neue Version mit Solana und BNB)", watch, data)):
            state["telegram_verified"] = True
    elif local.hour >= 8 and state.get("last_status_day") != local.date().isoformat():
        if tg_send(status_text("📊 Tagesstatus Trust Radar", watch, data)):
            state["last_status_day"] = local.date().isoformat()
    data["stats"] = STATS
    state.update({"last_run": now.isoformat(), "watch": watch})
    save_json(DATA_FILE, data)
    save_json(STATE_FILE, state)
    print(f"Fertig. {len(watch)} Coins in Beobachtung, {alerts} Nachrichten gesendet.")


if __name__ == "__main__":
    main()
