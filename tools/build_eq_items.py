import json
import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# ------------------------------------------------------------
# TibiaSweden EQ Catalog Builder
# - Hämtar seed-sidor från TibiaWiki (Fandom) via MediaWiki Action API (api.php)
# - Bygger en titel-lista (data/eq_titles.json)
# - Processar titlar i batchar (data/eq_state.json) och uppdaterar data/eq_items.json
#
# Viktigt:
# - Scriptet sparar "progress" i repo så GitHub Actions kan fortsätta nästa körning.
# - Det tar bara items som faktiskt har "protection ... %" i texten (för att hålla katalogen relevant).
# ------------------------------------------------------------

BASE = "https://tibia.fandom.com"
API  = f"{BASE}/api.php"

UA = {"User-Agent": "TibiaSweden-EQOpt/1.0 (catalog builder)"}

ELEMENTS = ["physical", "fire", "ice", "energy", "earth", "death", "holy"]
VOCS = ["knight", "paladin", "druid", "sorcerer", "monk"]

# Seed-sidor: en lagom bred start som brukar ge bra coverage
SEEDS = [
    ("helmet", "Helmets"),
    ("armor", "Armors"),
    ("legs", "Legs"),
    ("boots", "Boots"),
    ("shield", "Shields"),
    ("offhand", "Spellbooks"),
    ("ring", "Rings"),
    ("amulet", "Amulets_and_Necklaces"),
    ("_set", "Fire_Protection_Set"),
    ("_set", "Ice_Protection_Set"),
    ("_set", "Energy_Protection_Set"),
    ("_set", "Earth_Protection_Set"),
    ("_set", "Death_Protection_Set"),
    ("_set", "Holy_Protection_Set"),
]

# Robust regex: matchar både "protection fire 5%" och "fire +5%" varianter som förekommer i wiki-text
PROT_RE = re.compile(
    r"(?:protection\s+)?(physical|fire|ice|energy|earth|death|holy)\s*([+-]?\d+)\s*%",
    re.IGNORECASE
)

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def api_parse_html(page: str) -> str:
    # MediaWiki Action API: action=parse + prop=text ger HTML
    # (Detta är standard i MediaWiki-dokumentationen.)
    # https://www.mediawiki.org/wiki/API:Parsing_wikitext
    params = {
        "action": "parse",
        "page": page,
        "prop": "text",
        "format": "json",
        "origin": "*",
    }
    r = requests.get(API, params=params, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()["parse"]["text"]["*"]

def title_from_href(href: str):
    if not href.startswith("/wiki/"):
        return None
    title = href[len("/wiki/"):]
    title = title.split("#", 1)[0]
    bad_prefix = ("File:", "Category:", "Special:", "Template:", "Help:", "Talk:", "TibiaWiki:")
    if any(title.startswith(p) for p in bad_prefix):
        return None
    if title in ("Main_Page",):
        return None
    return title

def build_titles():
    # Bygg en titel-lista med slot-mappning när vi har den (från slot-seeds)
    titles = {}  # title -> slot
    for slot, page in SEEDS:
        html = api_parse_html(page)
        soup = BeautifulSoup(html, "html.parser")
        root = soup.select_one(".mw-parser-output") or soup

        for a in root.select("a[href]"):
            href = a.get("href", "")
            t = title_from_href(href)
            if not t:
                continue
            # slot från set-sidor är okänd om vi inte redan har den från slot-sidor
            if t not in titles:
                titles[t] = None if slot == "_set" else slot

        time.sleep(0.25)

    # Packa till lista för enklare state/index
    out = [{"title": t, "slot": titles[t]} for t in sorted(titles.keys())]
    return out

def parse_item(title: str):
    html = api_parse_html(title)
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    res = {}
    for m in PROT_RE.finditer(text):
        el = m.group(1).lower()
        val = int(m.group(2))
        if el in ELEMENTS:
            res[el] = val

    # Imbuement slots: ofta står "Empty Slot" i item-boxen
    imbue_slots = text.lower().count("empty slot")

    level = None
    m = re.search(r"of level\s+(\d+)\s+or higher", text, re.IGNORECASE)
    if m:
        level = int(m.group(1))

    voc = ["ANY"]
    low = text.lower()
    if "only be wielded properly by" in low:
        v = []
        if "knight" in low: v.append("KNIGHT")
        if "paladin" in low: v.append("PALADIN")
        if "druid" in low: v.append("DRUID")
        if "sorcerer" in low: v.append("SORCERER")
        if "monk" in low: v.append("MONK")
        voc = v if v else ["ANY"]

    return {
        "res": res,
        "imbueSlots": imbue_slots,
        "level": level,
        "voc": voc
    }

def main():
    titles_path = "data/eq_titles.json"
    state_path  = "data/eq_state.json"
    items_path  = "data/eq_items.json"

    titles = load_json(titles_path, None)
    if not titles:
        titles = build_titles()
        save_json(titles_path, titles)

    state = load_json(state_path, {
        "index": 0,
        "batchSize": 60,
        "createdAt": int(time.time()),
        "lastRun": None
    })

    items = load_json(items_path, [])
    existing = set((it.get("source") or it.get("name") or "").strip() for it in items)

    start = int(state.get("index", 0))
    batch = int(state.get("batchSize", 60))
    end = min(len(titles), start + batch)

    processed = 0
    added = 0

    for i in range(start, end):
        t = titles[i]["title"]
        slot = titles[i].get("slot") or "unknown"

        try:
            meta = parse_item(t)
        except Exception:
            # Skippa hårt om en sida failar (Fandom kan strula ibland)
            state["index"] = i + 1
            continue

        processed += 1
        time.sleep(0.25)

        if not meta["res"]:
            continue

        src = f"{BASE}/wiki/{quote(t)}"
        if src in existing:
            continue

        items.append({
            "name": t.replace("_", " "),
            "slot": slot,
            "level": meta["level"],
            "voc": meta["voc"],
            "res": meta["res"],
            "imbueSlots": meta["imbueSlots"],
            "source": src
        })
        existing.add(src)
        added += 1

    state["index"] = end
    state["lastRun"] = int(time.time())
    state["lastAdded"] = added
    state["lastProcessed"] = processed
    state["totalTitles"] = len(titles)
    state["totalItems"] = len(items)

    save_json(items_path, items)
    save_json(state_path, state)

    print(f"Titles: {len(titles)} | Processed: {processed} | Added: {added} | Items: {len(items)} | Next index: {state['index']}")

if __name__ == "__main__":
    main()
