# tools/build_eq_titles.py
# Builds data/eq_titles.json by querying TibiaWiki (Fandom) categories via MediaWiki API.
# This avoids "creature links" leaking in from list pages.

import json
import time
import datetime as dt
import urllib.parse
import urllib.request

API = "https://tibia.fandom.com/api.php"
HEADERS = {
    "User-Agent": "TibiaSweden-TibiaEQ/1.0 (github.com/YamiXs/Tibia-EQ)"
}

# Slot -> Category name (without "Category:")
SLOT_CATEGORIES = {
    "helmet": "Helmets",
    "armor": "Armors",
    "legs": "Legs",
    "boots": "Boots",
    "shield": "Shields",
    "spellbook": "Spellbooks",
    "amulet": "Amulets_and_Necklaces",
    "ring": "Rings",
    "quiver": "Quivers",
    # If you want "maximum coverage", you can also add broader categories like:
    # "body_equipment": "Body_Equipment",
}

def api_get(params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    url = f"{API}?{qs}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.load(resp)

def list_category_members(category_name: str) -> list[str]:
    titles: list[str] = []
    cont = None

    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{category_name}",
            "cmnamespace": 0,     # main namespace only
            "cmtype": "page",     # exclude subcats/files
            "cmlimit": 500,       # max per request (typical)
            "format": "json",
        }
        if cont:
            params["cmcontinue"] = cont

        data = api_get(params)
        members = data.get("query", {}).get("categorymembers", [])
        titles.extend([m["title"] for m in members if "title" in m])

        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break

        time.sleep(0.2)  # be polite

    return titles

def main():
    out = {
        "generatedAt": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "source": "tibia.fandom.com",
        "items": [],
        "slots": {},
        "count": 0,
    }

    seen = set()

    for slot, category in SLOT_CATEGORIES.items():
        titles = list_category_members(category)
        for title in titles:
            if title in seen:
                continue
            seen.add(title)
            out["items"].append({"title": title, "slot": slot})

        out["slots"][slot] = sum(1 for it in out["items"] if it["slot"] == slot)

    out["count"] = len(out["items"])

    with open("data/eq_titles.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"OK: wrote data/eq_titles.json with {out['count']} titles")

if __name__ == "__main__":
    main()
