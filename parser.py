import html
import re
import unicodedata
from collections import defaultdict


# ---------------------------------------------------------------------------
# Parsing philosophy
# ---------------------------------------------------------------------------
# Supplier text is treated as authoritative product text. We only:
#   1) detect a terminal retail price;
#   2) add a missing obvious brand from the current supplier section
#      (e.g. "iPhone: 17" + "🇯🇵 17 Pro ...");
#   3) ignore service/wholesale lines;
#   4) normalize superficial formatting ONLY for duplicate comparison.
# Country/region, memory, color, SIM/eSIM, CPO/ASIS, repair/replacement,
# activation state, model codes, size, year, bundle notes, etc. stay in name.


PRICE_RE = re.compile(
    r"(?P<price>(?:\d{1,3}(?:[\s\u00a0\u202f.,]\d{3})+|\d{3,8}))"
    r"\s*(?:₽|р\.?|руб\.?|rub|rur)?\s*$",
    re.I,
)

WHOLESALE_RE = re.compile(
    r"(?:"
    r"\bот\s*\d+\s*(?:шт|штук)\b"
    r"|\b\d+\s*(?:шт|штук)\s*(?:\+|и\s+более)?\b"
    r"|\bмелк(?:ий|ого)\s+опт\b"
    r"|\bкрупн(?:ый|ого)\s+опт\b"
    r"|\bопт(?:ом|овый|овая|овые)?\b"
    r"|\bwholesale\b"
    r")",
    re.I,
)

SERVICE_RE = re.compile(
    r"(?:"
    r"заказать\s*[-—:]?\s*@"
    r"|актуальн(?:ые|ая)\s+цен(?:ы|а)\s+по\s+запросу"
    r"|цен(?:ы|а)\s+по\s+запросу"
    r"|^dm\s+mobile\s+price\s*:?$"
    r"|^прайс(?:\s*лист)?\s*:?$"
    r")",
    re.I,
)

SEPARATOR_RE = re.compile(r"^[\s\-–—_=━─•·.]+$")

# A small confusable map only for duplicate keys. Display text is never changed.
CONFUSABLES = str.maketrans({
    "а": "a", "А": "a",
    "е": "e", "Е": "e",
    "о": "o", "О": "o",
    "р": "p", "Р": "p",
    "с": "c", "С": "c",
    "х": "x", "Х": "x",
    "у": "y", "У": "y",
    "к": "k", "К": "k",
    "м": "m", "М": "m",
    "т": "t", "Т": "t",
    "в": "b", "В": "b",
    "н": "h", "Н": "h",
})

COUNTRY_FLAG_RE = re.compile(r"^(?P<flags>(?:[\U0001F1E6-\U0001F1FF]{2}\s*)+)(?P<rest>.*)$")

CATEGORY_PATTERNS = [
    (re.compile(r"^iphone\s*:\s*(?:13\s*[-–—]\s*14\s*[-–—]\s*15|13-14-15)\s*$", re.I), "iphone", "iPhone"),
    (re.compile(r"^iphone\s*:\s*1[2-7](?:\s*[-–—]\s*1[2-7])?\s*$", re.I), "iphone", "iPhone"),
    (re.compile(r"^iphone\s*$", re.I), "iphone", "iPhone"),
    (re.compile(r"^samsung\s+блоч(?:ки|ок|ки)\s*$", re.I), "samsung_accessories", "Samsung аксессуары"),
    (re.compile(r"^samsung\s*$", re.I), "samsung", "Samsung"),
    (re.compile(r"^honor\s*$", re.I), "honor", "Honor"),
    (re.compile(r"^huawei\s*$", re.I), "huawei", "Huawei"),
    (re.compile(r"^tecno\s*$", re.I), "tecno", "Tecno"),
    (re.compile(r"^(?:ray\s*-?\s*ban|rayban)\s*$", re.I), "rayban", "Ray-Ban"),
    (re.compile(r"^apple\s+watch\s*$", re.I), "apple_watch", "Apple Watch"),
    (re.compile(r"^ipad\s*$", re.I), "ipad", "iPad"),
    (re.compile(r"^macbook\s*$", re.I), "macbook", "MacBook"),
    (re.compile(r"^airpods\s*$", re.I), "airpods", "AirPods"),
    (re.compile(r"^dyson\s*$", re.I), "dyson", "Dyson"),
    (re.compile(r"^(?:playstation|sony\s+playstation)\s*$", re.I), "playstation", "PlayStation"),
    (re.compile(r"^аксессуары\s+apple(?:\s+оригинал)?\s*$", re.I), "apple_accessories", "Apple аксессуары"),
]

# Stable display order. Unknown brands go after these alphabetically.
BLOCK_PRIORITY = [
    "iphone_12", "iphone_13", "iphone_14", "iphone_15",
    "iphone_16e", "iphone_16", "iphone_16_plus", "iphone_16_pro", "iphone_16_pro_max",
    "iphone_17e", "iphone_17", "iphone_17_air", "iphone_17_pro", "iphone_17_pro_max",
    "iphone_other",
    "apple_watch", "ipad", "macbook", "airpods", "apple_accessories",
    "samsung_buds", "samsung_a", "samsung_s", "samsung_fold_flip", "samsung_tablets", "samsung_other", "samsung_accessories",
    "honor", "huawei", "tecno", "rayban", "dyson", "playstation",
]

BLOCK_TITLES = {
    "iphone_12": "🍎 iPhone 12",
    "iphone_13": "🍎 iPhone 13",
    "iphone_14": "🍎 iPhone 14",
    "iphone_15": "🍎 iPhone 15",
    "iphone_16e": "🍎 iPhone 16e",
    "iphone_16": "🍎 iPhone 16",
    "iphone_16_plus": "🍎 iPhone 16 Plus",
    "iphone_16_pro": "🍎 iPhone 16 Pro",
    "iphone_16_pro_max": "🍎 iPhone 16 Pro Max",
    "iphone_17e": "🍎 iPhone 17e",
    "iphone_17": "🍎 iPhone 17",
    "iphone_17_air": "🍎 iPhone 17 Air",
    "iphone_17_pro": "🍎 iPhone 17 Pro",
    "iphone_17_pro_max": "🍎 iPhone 17 Pro Max",
    "iphone_other": "🍎 iPhone",
    "apple_watch": "⌚ Apple Watch",
    "ipad": "🍎 iPad",
    "macbook": "💻 MacBook",
    "airpods": "🎧 AirPods",
    "apple_accessories": "🔌 Apple аксессуары",
    "samsung_buds": "🎧 Samsung Buds",
    "samsung_a": "📱 Samsung Galaxy A",
    "samsung_s": "📱 Samsung Galaxy S",
    "samsung_fold_flip": "📱 Samsung Fold / Flip",
    "samsung_tablets": "📲 Samsung Tablets",
    "samsung_other": "📱 Samsung",
    "samsung_accessories": "🔌 Samsung аксессуары",
    "honor": "📱 Honor",
    "huawei": "📱 Huawei",
    "tecno": "📱 Tecno",
    "rayban": "👓 Ray-Ban",
    "dyson": "📦 Dyson",
    "playstation": "🎮 PlayStation",
}


def norm(text: str) -> str:
    return " ".join(
        (text or "")
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
        .split()
    )


def closed_match(text: str, closed_text: str = "") -> bool:
    value = norm(text).casefold()
    expected = norm(closed_text).casefold()
    if expected and expected in value:
        return True
    markers = (
        "мы закрыты",
        "поставщик закрыт",
        "продажи закрыты",
        "сейчас закрыты",
        "в данный момент мы закрыты",
        "currently closed",
    )
    return any(marker in value for marker in markers)


def is_wholesale_line(line: str) -> bool:
    return bool(WHOLESALE_RE.search(norm(line)))


def is_service_line(line: str) -> bool:
    txt = norm(line).strip()
    if not txt:
        return True
    if SEPARATOR_RE.match(txt):
        return True
    txt2 = txt.lstrip("> ")
    if SERVICE_RE.search(txt2):
        return True
    return False


def _clean_leading_decoration(text: str) -> str:
    txt = norm(text).strip(" |:;")
    # Keep country flags. Remove bullets/glasses/etc. before real text.
    while txt and not (txt[0].isalnum() or txt[0] in "🇦🇧🇨🇩🇪🇫🇬🇭🇮🇯🇰🇱🇲🇳🇴🇵🇶🇷🇸🇹🇺🇻🇼🇽🇾🇿"):
        txt = txt[1:].lstrip()
    return norm(txt)


def _split_flags(text: str):
    txt = norm(text)
    m = COUNTRY_FLAG_RE.match(txt)
    if not m:
        return "", txt
    return norm(m.group("flags")), norm(m.group("rest"))


def _insert_brand_after_flags(name: str, brand: str) -> str:
    flags, rest = _split_flags(name)
    if rest.casefold().startswith(brand.casefold()):
        return name
    value = f"{brand} {rest}".strip()
    return f"{flags} {value}".strip() if flags else value


def extract_price(line: str):
    """Return (name_without_price, price) or (None, None)."""
    txt = norm(line)
    if not txt or is_wholesale_line(txt) or is_service_line(txt):
        return None, None

    m = PRICE_RE.search(txt)
    if not m:
        return None, None

    digits = re.sub(r"\D", "", m.group("price"))
    if not digits:
        return None, None

    try:
        price = int(digits)
    except ValueError:
        return None, None

    # Wide but realistic electronics range. 300 ₽ keeps small spare parts.
    if price < 300 or price > 10_000_000:
        return None, None

    before = txt[:m.start("price")]
    # Remove price separator only; preserve everything else.
    before = re.sub(r"[\s|:;]*[-–—]?\s*$", "", before)
    name = _clean_leading_decoration(before)
    if not name or not re.search(r"[A-Za-zА-Яа-я]", name):
        return None, None

    return name, price


def detect_category_header(line: str):
    txt = norm(line).strip(" :;-–—")
    txt = txt.lstrip("> ")
    txt = _clean_leading_decoration(txt).strip(" :;-–—")
    for pattern, key, title in CATEGORY_PATTERNS:
        if pattern.match(txt):
            return key, title
    return None


def _iphone_family_from_name(name: str):
    _, txt = _split_flags(name)
    txt = txt.casefold()
    txt = re.sub(r"^iphone\s+", "", txt)

    patterns = [
        (r"^17\s+pro\s+max\b", "iphone_17_pro_max"),
        (r"^17\s+pro\b", "iphone_17_pro"),
        (r"^17\s+air\b", "iphone_17_air"),
        (r"^17[еe]\b", "iphone_17e"),
        (r"^17\b", "iphone_17"),
        (r"^16\s+pro\s+max\b", "iphone_16_pro_max"),
        (r"^16\s+pro\b", "iphone_16_pro"),
        (r"^16\s+plus\b", "iphone_16_plus"),
        (r"^16[еe]\b", "iphone_16e"),
        (r"^16\b", "iphone_16"),
        (r"^15\b", "iphone_15"),
        (r"^14\b", "iphone_14"),
        (r"^13\b", "iphone_13"),
        (r"^12\b", "iphone_12"),
    ]
    for pattern, key in patterns:
        if re.search(pattern, txt, re.I):
            return key
    return "iphone_other" if txt.startswith(tuple(str(x) for x in range(10))) or "iphone" in name.casefold() else None


def _looks_explicit_brand(name: str) -> bool:
    _, rest = _split_flags(name)
    low = rest.casefold()
    prefixes = (
        "iphone ", "samsung ", "galaxy ", "honor ", "huawei ", "tecno ",
        "ray-ban ", "rayban ", "apple watch ", "ipad ", "macbook ",
        "airpods ", "dyson ", "sony ", "playstation ", "earpods ",
    )
    return low.startswith(prefixes)


def apply_context(name: str, context_key: str = None, subcontext: str = None) -> str:
    name = _clean_leading_decoration(name)
    flags, rest = _split_flags(name)
    low = rest.casefold()

    if context_key == "iphone":
        if re.match(r"^1[2-7](?:[еe]\b|\b|\s)", rest, re.I):
            rest = f"iPhone {rest}"

    elif context_key == "samsung":
        if not low.startswith(("samsung ", "galaxy ")):
            if re.match(r"^(?:buds\b|book\b|tab\b|a\d|s\d|z\s*(?:fold|flip)|fold\b|flip\b)", rest, re.I):
                rest = f"Samsung {rest}"

    elif context_key == "samsung_accessories":
        if not low.startswith("samsung "):
            rest = f"Samsung {rest}"

    elif context_key == "apple_watch":
        if not low.startswith("apple watch ") and re.match(r"^(?:se\b|s\d+\b|series\b|ultra\b)", rest, re.I):
            rest = f"Apple Watch {rest}"

    elif context_key == "ipad":
        if not low.startswith("ipad ") and re.match(r"^(?:mini\b|air\b|pro\b|\d+\b)", rest, re.I):
            rest = f"iPad {rest}"

    elif context_key == "macbook":
        if not low.startswith("macbook "):
            rest = f"MacBook {rest}"

    elif context_key == "airpods":
        if not low.startswith("airpods "):
            # Spare parts under an AirPods model subsection.
            if subcontext and re.match(r"^(?:кейс\b|ухо\b|амбушюр)", rest, re.I):
                rest = f"{subcontext} {rest}"
            elif re.match(r"^(?:кейс\b|ухо\b|амбушюр)", rest, re.I):
                rest = f"AirPods {rest}"

    elif context_key == "honor" and not low.startswith("honor "):
        rest = f"Honor {rest}"
    elif context_key == "huawei" and not low.startswith("huawei "):
        rest = f"Huawei {rest}"
    elif context_key == "tecno" and not low.startswith("tecno "):
        rest = f"Tecno {rest}"
    elif context_key == "rayban" and not low.startswith(("ray-ban ", "rayban ")):
        rest = f"Ray-Ban {rest}"
    elif context_key == "dyson" and not low.startswith("dyson "):
        rest = f"Dyson {rest}"
    elif context_key == "playstation" and not low.startswith(("sony ", "playstation ")):
        rest = f"PlayStation {rest}"

    full = norm(rest)
    return f"{flags} {full}".strip() if flags else full


def detect_block(name: str, context_key: str = None):
    low = name.casefold()
    no_flags = _split_flags(name)[1]
    rest = no_flags.casefold()

    iphone_key = _iphone_family_from_name(name)
    if iphone_key:
        return iphone_key, BLOCK_TITLES.get(iphone_key, "🍎 iPhone")

    if rest.startswith("apple watch ") or context_key == "apple_watch":
        return "apple_watch", BLOCK_TITLES["apple_watch"]
    if rest.startswith("ipad ") or context_key == "ipad":
        return "ipad", BLOCK_TITLES["ipad"]
    if rest.startswith("macbook ") or context_key == "macbook":
        return "macbook", BLOCK_TITLES["macbook"]
    if rest.startswith("airpods ") or context_key == "airpods":
        return "airpods", BLOCK_TITLES["airpods"]
    if context_key == "apple_accessories" or rest.startswith(("earpods ", "magsafe ", "кабель ", "переходник ", "зу apple ")):
        return "apple_accessories", BLOCK_TITLES["apple_accessories"]

    if context_key == "samsung_accessories":
        return "samsung_accessories", BLOCK_TITLES["samsung_accessories"]

    if rest.startswith(("samsung ", "galaxy ")) or context_key == "samsung":
        clean = re.sub(r"^(?:samsung\s+)?(?:galaxy\s+)?", "", rest, flags=re.I)
        if re.match(r"^buds\b", clean, re.I):
            return "samsung_buds", BLOCK_TITLES["samsung_buds"]
        if re.match(r"^(?:tab\b|book\b)", clean, re.I):
            return "samsung_tablets", BLOCK_TITLES["samsung_tablets"]
        if re.match(r"^a\d", clean, re.I):
            return "samsung_a", BLOCK_TITLES["samsung_a"]
        if re.match(r"^s\d", clean, re.I):
            return "samsung_s", BLOCK_TITLES["samsung_s"]
        if re.match(r"^(?:z\s*)?(?:fold|flip)\b", clean, re.I):
            return "samsung_fold_flip", BLOCK_TITLES["samsung_fold_flip"]
        return "samsung_other", BLOCK_TITLES["samsung_other"]

    fixed = (
        ("honor", "honor"), ("huawei", "huawei"), ("tecno", "tecno"),
        ("ray-ban", "rayban"), ("rayban", "rayban"), ("dyson", "dyson"),
        ("sony playstation", "playstation"), ("playstation", "playstation"),
    )
    for prefix, key in fixed:
        if rest.startswith(prefix + " ") or rest == prefix or context_key == key:
            return key, BLOCK_TITLES[key]

    # Generic fallback: use the first word as a stable brand key.
    word_match = re.search(r"[A-Za-zА-Яа-я][A-Za-zА-Яа-я0-9-]*", no_flags)
    if not word_match:
        return "other", "📦 Другое"
    brand = word_match.group(0)
    safe = re.sub(r"[^a-z0-9]+", "_", unicodedata.normalize("NFKD", brand).encode("ascii", "ignore").decode().casefold()).strip("_")
    if not safe:
        safe = re.sub(r"\W+", "_", brand.casefold(), flags=re.UNICODE).strip("_") or "other"
    return f"brand_{safe}", f"📦 {brand}"


def canonical_name(name: str) -> str:
    """Normalize superficial formatting only; preserve all real attributes."""
    txt = unicodedata.normalize("NFKC", norm(name)).translate(CONFUSABLES).casefold()
    txt = txt.replace("ё", "е")
    txt = re.sub(r"\bгб\b", "gb", txt)
    txt = re.sub(r"\bтб\b", "tb", txt)
    txt = re.sub(r"\b(\d+)\s*gb\b", r"\1gb", txt)
    txt = re.sub(r"\b(\d+)\s*tb\b", r"\1tb", txt)
    txt = re.sub(r"\be[\s-]*sim\b", "esim", txt)
    txt = re.sub(r"\b2\s*sim\b", "2sim", txt)
    txt = re.sub(r"\b1\s*sim\b", "1sim", txt)
    txt = re.sub(r"[|,;:/()\[\]{}]+", " ", txt)
    txt = re.sub(r"[-–—]+", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def _natural_key(text: str):
    parts = re.split(r"(\d+)", canonical_name(text))
    return tuple(int(p) if p.isdigit() else p for p in parts)


def parse_supplier_price(text: str, source: str = ""):
    products = []
    context_key = None
    context_title = None
    subcontext = None

    for raw in (text or "").splitlines():
        line = norm(raw)
        if not line:
            continue

        # Telegram export quote marker.
        stripped = line.lstrip("> ")
        header = detect_category_header(stripped)
        if header:
            context_key, context_title = header
            subcontext = None
            continue

        if is_service_line(stripped) or is_wholesale_line(stripped):
            continue

        name, price = extract_price(stripped)
        if price is None:
            # Only a few no-price headings affect subsequent part names.
            short = _clean_leading_decoration(stripped).strip(" :")
            if context_key == "airpods" and re.match(r"^airpods\b", short, re.I):
                subcontext = short
            continue

        full_name = apply_context(name, context_key, subcontext)
        block_key, block_title = detect_block(full_name, context_key)

        products.append({
            "name": full_name,
            "price": int(price),
            "block_key": block_key,
            "block_title": block_title,
            "source": source,
            "identity": canonical_name(full_name),
        })

    # Remove exact duplicates inside one source, keeping the lower price.
    best = {}
    for product in products:
        key = product["identity"]
        current = best.get(key)
        if current is None or product["price"] < current["price"]:
            best[key] = product

    result = list(best.values())
    result.sort(key=lambda p: (_block_rank(p["block_key"], p["block_title"]), _natural_key(p["name"])))
    return result


def _block_rank(key: str, title: str):
    try:
        return (0, BLOCK_PRIORITY.index(key))
    except ValueError:
        return (1, (title or key).casefold())


def merge_products(*product_lists):
    """Merge two supplier lists. Same full variant -> lower purchase price wins."""
    best = {}
    for products in product_lists:
        for product in products or []:
            key = product.get("identity") or canonical_name(product["name"])
            current = best.get(key)
            if current is None or int(product["price"]) < int(current["price"]):
                best[key] = dict(product)

    merged = list(best.values())
    merged.sort(key=lambda p: (_block_rank(p["block_key"], p["block_title"]), _natural_key(p["name"])))
    return merged


def group_products(products):
    groups = defaultdict(list)
    titles = {}
    for product in products or []:
        key = product["block_key"]
        groups[key].append(product)
        titles[key] = product.get("block_title") or BLOCK_TITLES.get(key, key)

    ordered = sorted(groups, key=lambda k: _block_rank(k, titles.get(k, k)))
    return [(key, titles[key], groups[key]) for key in ordered]


def format_price(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def render_product_line(product, markup_amount=0):
    name = html.escape(norm(product["name"]))
    final_price = int(product["price"]) + int(markup_amount or 0)
    # Inline code is intentionally used so Telegram clients expose the name as
    # a convenient copyable fragment on tap/long-press, without copying price.
    return f"<code>{name}</code> — <b>{format_price(final_price)}</b> ₽"


# Compatibility helper used by a few old imports/tools.
def parse_full_price(text: str):
    products = parse_supplier_price(text)
    blocks = defaultdict(list)
    block_titles = {}
    for product in products:
        blocks[product["block_key"]].append(
            f"{product['name']} - {product['price']}"
        )
        block_titles[product["block_key"]] = product["block_title"]
    return {
        "full_lines": [norm(x) for x in (text or "").splitlines() if norm(x)],
        "blocks": dict(blocks),
        "block_titles": block_titles,
    }
