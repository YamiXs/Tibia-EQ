# tools/build_eq_items.py
import json
import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# ------------------------------------------------------------
# TibiaSweden EQ Catalog Builder
# - Bygger en titel-lista (data/eq_titles.json) från slot-sidor + set-sidor
#   (OBS: Denna metod kan plocka upp "skräp-länkar" — men vi har starka guards
#   som filtrerar bort creatures och icke-item-sidor.)
# - Processar titlar i batchar (data/eq_state.json) och uppdaterar data/eq_items.json
#
# Viktigt:
# - Scriptet sparar "progress" i repo så GitHub Actions kan fortsätta nästa körning.
# - Det tar bara items som faktiskt har protection/resist-info.
# ------------------------------------------------------------

BASE = "https://tibia.fandom.com"
API  = f"{BASE}/api.php"

UA = {"User-Agent": "TibiaSweden-EQOpt/1.1 (catalog builder)"}

ELEMENTS = ["physical", "fire", "ice", "energy", "earth", "death", "holy"]

# Seed-sidor: bra bredd, men ger ibland extralänkar (guards tar hand om det)
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

# Matchar både "protection fire 5%" och "fire 5%"
PROT_RE = re.compile(
    r"(?:protection\s+)?(physical|fire|ice|energy|earth|death|holy)\s*([+-]?\d+)\s*%",
    re.IGNORECASE
)

# ------------------------------------------------------------
# Guards (superviktigt)
# ------------------------------------------------------------
def looks_like_creature(page_text: str) -> bool:
    # Typiska signaler på creature-sidor på TibiaWiki/Fandom
    return bool(re.search(r"\bHitpoints\b|\bExperience Points\b|\bBestiary\b|\bCreature\b", page_text, re.I))

def looks_like_item(page_text: str) -> bool:
    # Typiska signaler på item-sidor (räcker för filtrering)
    return bool(re.search(r"\bImbuements?\b|\bIt weighs\b|\bYou see\b|\bArm:\b|\bProtection\b", page_text, re.I))

# ------------------------------------------------------------
# IO helpers
# ------------------------------------------------------------
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ------------------------------------------------------------
# MediaWiki parse API
# ------------------------------------------------------------
def api_parse_html(page: str) -> str:
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

# ------------------------------------------------------------
# Build titles (seed pages -> titles)
# ------------------------------------------------------------
def build_titles():
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
            if t not in titles:
                titles[t] = None if slot == "_set" else slot

        time.sleep(0.25)

    out = [{"title": t, "slot": titles[t]} for t in sorted(titles.keys())]
    return out

# ------------------------------------------------------------
# Parse one item page
# ------------------------------------------------------------
def parse_item(title: str):
    html = api_parse_html(title)
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    # ✅ Guard: släpp inte igenom creatures / icke-items
    if looks_like_creature(text) or not looks_like_item(text):
        return None

    res = {}
    for m in PROT_RE.finditer(text):
        el = m.group(1).lower()
        val = int(m.group(2))
        if el in ELEMENTS:
            res[el] = val

    # Imbuement slots: ofta “Empty Slot” i item-boxen
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

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
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
    skipped_non_item = 0

    for i in range(start, end):
        t = titles[i]["title"]
        slot = titles[i].get("slot") or "unknown"

        try:
            meta = parse_item(t)
        except Exception:
            state["index"] = i + 1
            continue

        processed += 1
        time.sleep(0.25)

        if meta is None:
            skipped_non_item += 1
            continue

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
    state["lastSkippedNonItem"] = skipped_non_item
    state["totalTitles"] = len(titles)
    state["totalItems"] = len(items)

    save_json(items_path, items)
    save_json(state_path, state)

    print(
        f"Titles: {len(titles)} | Processed: {processed} | "
        f"SkippedNonItem: {skipped_non_item} | Added: {added} | "
        f"Items: {len(items)} | Next index: {state['index']}"
    )

if __name__ == "__main__":
    main()
