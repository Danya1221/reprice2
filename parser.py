import re

BLOCK_KEYS = (
    "12",
    "13",
    "14",
    "15_sim",
    "15_esim",
    "16_sim",
    "16_esim",
    "17_sim",
    "17_esim",
    "other",
)

BLOCK_TITLES = {
    "12": "🍏 Apple iPhone 12",
    "13": "🍏 Apple iPhone 13",
    "14": "🍏 Apple iPhone 14",
    "15_sim": "🍏 Apple iPhone 15 — SIM",
    "15_esim": "🍏 Apple iPhone 15 — eSIM",
    "16_sim": "🍏 Apple iPhone 16 — SIM",
    "16_esim": "🍏 Apple iPhone 16 — eSIM",
    "17_sim": "🍏 Apple iPhone 17 — SIM",
    "17_esim": "🍏 Apple iPhone 17 — eSIM",
    "other": "📦 Остальное",
}

HEADERISH_RE = re.compile(
    r"^\s*(?:iphone\s*)?(12|13|14|15|16|17)\s*(?:series|серия)?\s*$",
    re.I,
)

# Цена поставщика в текущем формате:
# "17 Pro Max 256 Blue (eSim) - 100700"
PRICE_END_RE = re.compile(
    r"(?P<prefix>\s[-–—]\s*)"
    r"(?P<price>\d[\d\s\u00a0\u202f]*)"
    r"(?P<suffix>\s*(?:₽|руб\.?|rub)?\s*)$",
    re.I,
)

NON_US_FLAGS = (
    "🇮🇳", "🇨🇳", "🇦🇪", "🇯🇵", "🇭🇰", "🇸🇬",
    "🇨🇦", "🇬🇧", "🇪🇺", "🇰🇷", "🇻🇳", "🇹🇭",
    "🇦🇺", "🇩🇪", "🇫🇷", "🇮🇹", "🇪🇸",
)


def norm(text: str) -> str:
    return " ".join(
        (text or "")
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
        .split()
    )


def closed_match(text: str, closed_text: str) -> bool:
    """
    Не зависим от точки/переноса строки.
    """
    value = norm(text).casefold()
    expected = norm(closed_text).casefold()

    if expected and expected in value:
        return True

    return (
        "мы закрыты" in value
        and "старт" in value
        and "продаж" in value
    )


def generation_in_line(line: str):
    txt = norm(line)

    # Поддерживает 16E / 16Е / 17e / 17 Air / 17 Pro Max и т.п.
    m = re.match(
        r"^(?:iphone\s*)?(12|13|14|15|16|17)(?!\d)",
        txt,
        re.I,
    )
    return m.group(1) if m else None


def explicit_sim_type(line: str):
    """
    Проверяем КАЖДУЮ строку независимо.

    sim  = есть физическая SIM
    esim = eSIM-only
    """
    txt = norm(line)
    compact = re.sub(r"[\s\-]", "", txt.casefold())

    # Сначала 1Sim+eSim, потому что внутри есть слово eSim.
    if (
        "1sim+esim" in compact
        or "1sim/esim" in compact
        or "1sim+еsim" in compact
    ):
        return "sim"

    if re.search(
        r"\(\s*1\s*sim\s*\+\s*e[\s\-]?sim\s*\)",
        txt,
        re.I,
    ):
        return "sim"

    if re.search(
        r"\(\s*e[\s\-]?sim\s*\)",
        txt,
        re.I,
    ):
        return "esim"

    if re.search(
        r"\(\s*(?:1\s*)?sim\s*\)",
        txt,
        re.I,
    ):
        return "sim"

    return None


def detect_sim_type(line: str, generation: str):
    explicit = explicit_sim_type(line)

    # Если поставщик явно указал тип — это главный источник.
    if explicit:
        return explicit

    txt = norm(line)

    # Для 15/16 в текущем прайсе тип задаётся регионом.
    # Американские версии 14+ — eSIM-only.
    if generation in {"15", "16"}:
        if "🇺🇸" in txt:
            return "esim"

        if any(flag in txt for flag in NON_US_FLAGS):
            return "sim"

        # Не угадываем неизвестный регион.
        return None

    # Для 17 поставщик сам пишет (eSim)/(1Sim+eSim).
    # Если пометки нет — не смешиваем блоки.
    if generation == "17":
        return None

    return None


def is_context_header(line: str):
    txt = norm(line).casefold().strip(":-—–|")

    if not txt:
        return True

    if txt in {
        "sim",
        "esim",
        "e-sim",
        "e sim",
        "physical sim",
        "dual sim",
    }:
        return True

    return bool(HEADERISH_RE.match(norm(line)))


def is_active_item(line: str):
    """
    Активированные позиции поставщика в наш прайс не публикуем.
    Ловит: Актив, (Актив), АКТИВ и т.п.
    """
    txt = norm(line).casefold()
    return "актив" in txt


def iphone_family(line: str, generation: str):
    """
    Определяет модель внутри поколения.
    Проверяем самые длинные названия первыми, чтобы Pro Max не стал Pro.
    """
    txt = norm(line)
    gen = re.escape(str(generation))

    patterns = []

    if generation in {"12", "13"}:
        patterns = [
            (rf"^(?:iphone\s*)?{gen}\s+pro\s+max\b", f"{generation}_pro_max"),
            (rf"^(?:iphone\s*)?{gen}\s+pro\b", f"{generation}_pro"),
            (rf"^(?:iphone\s*)?{gen}\s+mini\b", f"{generation}_mini"),
            (rf"^(?:iphone\s*)?{gen}(?!\d)", generation),
        ]

    elif generation in {"14", "15"}:
        patterns = [
            (rf"^(?:iphone\s*)?{gen}\s+pro\s+max\b", f"{generation}_pro_max"),
            (rf"^(?:iphone\s*)?{gen}\s+pro\b", f"{generation}_pro"),
            (rf"^(?:iphone\s*)?{gen}\s+plus\b", f"{generation}_plus"),
            (rf"^(?:iphone\s*)?{gen}(?!\d)", generation),
        ]

    elif generation == "16":
        patterns = [
            (r"^(?:iphone\s*)?16e(?!\d)", "16e"),
            (r"^(?:iphone\s*)?16[еe](?!\d)", "16e"),
            (r"^(?:iphone\s*)?16\s+pro\s+max\b", "16_pro_max"),
            (r"^(?:iphone\s*)?16\s+pro\b", "16_pro"),
            (r"^(?:iphone\s*)?16\s+plus\b", "16_plus"),
            (r"^(?:iphone\s*)?16(?!\d)", "16"),
        ]

    elif generation == "17":
        patterns = [
            (r"^(?:iphone\s*)?17e(?!\d)", "17e"),
            (r"^(?:iphone\s*)?17[еe](?!\d)", "17e"),
            (r"^(?:iphone\s*)?17\s+pro\s+max\b", "17_pro_max"),
            (r"^(?:iphone\s*)?17\s+pro\b", "17_pro"),
            (r"^(?:iphone\s*)?17\s+air\b", "17_air"),
            (r"^(?:iphone\s*)?17(?!\d)", "17"),
        ]

    else:
        patterns = [
            (rf"^(?:iphone\s*)?{gen}(?!\d)", generation),
        ]

    for pattern, family in patterns:
        if re.match(pattern, txt, re.I):
            return family

    return None


def looks_like_product(line: str):
    txt = norm(line)

    if len(txt) < 5 or is_context_header(txt):
        return False

    return any(ch.isdigit() for ch in txt)


def display_product_line(line: str) -> str:
    """
    Для публикации добавляет префикс iPhone к товарной строке,
    если поставщик пишет только модель:
      16 Pro Max ... -> iPhone 16 Pro Max ...
      17e ...        -> iPhone 17e ...
    Уже существующий iPhone не дублируем.
    """
    txt = norm(line)

    if not txt:
        return txt

    if txt.casefold().startswith("iphone "):
        return txt

    if generation_in_line(txt):
        return f"iPhone {txt}"

    return txt


def add_markup_to_line(line: str, markup_amount: int) -> str:
    """
    Меняет только цену ПОСЛЕ последнего тире.
    Память 128/256/512, 1TB/2TB и модель не затрагиваются.
    """
    amount = int(markup_amount or 0)

    if amount == 0:
        return norm(line)

    txt = norm(line)
    match = PRICE_END_RE.search(txt)

    if not match:
        return txt

    raw_price = re.sub(r"\s+", "", match.group("price"))

    try:
        price = int(raw_price)
    except ValueError:
        return txt

    new_price = price + amount

    return (
        txt[:match.start("price")]
        + str(new_price)
        + (match.group("suffix") or "")
    ).strip()


def parse_full_price(text: str):
    """
    Полный прайс сохраняется всегда.
    Здесь только классифицируем товарные строки для блоков.
    """
    full_lines = []
    blocks = {key: [] for key in BLOCK_KEYS}

    for raw in (text or "").splitlines():
        line = norm(raw)

        if not line:
            continue

        full_lines.append(line)

        if is_context_header(line) or not looks_like_product(line):
            continue

        # Активированные позиции не публикуем вообще.
        if is_active_item(line):
            continue

        gen = generation_in_line(line)

        if gen in {"12", "13", "14"}:
            blocks[gen].append(line)
            continue

        if gen in {"15", "16", "17"}:
            sim_type = detect_sim_type(line, gen)

            if sim_type == "sim":
                blocks[f"{gen}_sim"].append(line)
            elif sim_type == "esim":
                blocks[f"{gen}_esim"].append(line)
            else:
                blocks["other"].append(line)

            continue

        blocks["other"].append(line)

    return {
        "full_lines": full_lines,
        "blocks": blocks,
    }


def parse_price(text: str):
    return parse_full_price(text)["blocks"]


IPHONE_SECTION_TITLES = {
    "12_mini": "iPhone 12 mini",
    "12": "iPhone 12",
    "12_pro": "iPhone 12 Pro",
    "12_pro_max": "iPhone 12 Pro Max",

    "13_mini": "iPhone 13 mini",
    "13": "iPhone 13",
    "13_pro": "iPhone 13 Pro",
    "13_pro_max": "iPhone 13 Pro Max",

    "14": "iPhone 14",
    "14_plus": "iPhone 14 Plus",
    "14_pro": "iPhone 14 Pro",
    "14_pro_max": "iPhone 14 Pro Max",

    "15": "iPhone 15",
    "15_plus": "iPhone 15 Plus",
    "15_pro": "iPhone 15 Pro",
    "15_pro_max": "iPhone 15 Pro Max",

    "16e": "iPhone 16e",
    "16": "iPhone 16",
    "16_plus": "iPhone 16 Plus",
    "16_pro": "iPhone 16 Pro",
    "16_pro_max": "iPhone 16 Pro Max",

    "17e": "iPhone 17e",
    "17": "iPhone 17",
    "17_air": "iPhone 17 Air",
    "17_pro": "iPhone 17 Pro",
    "17_pro_max": "iPhone 17 Pro Max",
}

IPHONE_FAMILY_ORDER = {
    "12": ("12_mini", "12", "12_pro", "12_pro_max"),
    "13": ("13_mini", "13", "13_pro", "13_pro_max"),
    "14": ("14", "14_plus", "14_pro", "14_pro_max"),
    "15": ("15", "15_plus", "15_pro", "15_pro_max"),
    "16": ("16e", "16", "16_plus", "16_pro", "16_pro_max"),
    "17": ("17e", "17", "17_air", "17_pro", "17_pro_max"),
}


def group_generation_lines(lines, generation):
    """
    Группирует строки одного поколения в правильном порядке моделей.
    Возвращает [(family_key, rows), ...].
    """
    order = IPHONE_FAMILY_ORDER.get(str(generation), (str(generation),))
    groups = {key: [] for key in order}
    other = []

    for line in lines or []:
        family = iphone_family(line, str(generation))

        if family in groups:
            groups[family].append(line)
        else:
            other.append(line)

    result = []

    for key in order:
        if groups[key]:
            result.append((key, groups[key]))

    if other:
        result.append(("other", other))

    return result


CATALOG_GROUP_TITLES = {
    "dyson_hair": "Dyson — фены и стайлеры",
    "dyson_vacuum": "Dyson — пылесосы",
    "xiaomi_redmi": "Xiaomi / Redmi / Poco",
    "gopro_insta360": "GoPro / Insta360",
    "meta_glasses": "Meta / Ray-Ban / Oakley",
    "nintendo": "Nintendo",
    "audio": "Аудио — Marshall / Harman Kardon / HyperX",
    "photo": "Фото — Kodak / Fujifilm",
    "apple_other": "Apple TV / Beats",
    "plaud": "Plaud",
    "other": "Другое",
}

CATALOG_NAV_LABELS = {
    "dyson_hair": "Dyson фены",
    "dyson_vacuum": "Dyson пылесосы",
    "xiaomi_redmi": "Xiaomi / Redmi",
    "gopro_insta360": "GoPro / Insta360",
    "meta_glasses": "Meta / Ray-Ban",
    "nintendo": "Nintendo",
    "audio": "Аудио",
    "photo": "Фото",
    "apple_other": "Apple TV / Beats",
    "plaud": "Plaud",
    "other": "Другое",
}

CATALOG_GROUP_ORDER = (
    "dyson_hair",
    "dyson_vacuum",
    "xiaomi_redmi",
    "gopro_insta360",
    "meta_glasses",
    "nintendo",
    "audio",
    "photo",
    "apple_other",
    "plaud",
    "other",
)


def detect_catalog_group(line: str) -> str:
    """
    Conservative classifier for non-iPhone products.
    """
    txt = norm(line)
    low = txt.casefold()

    if low.startswith("dyson "):
        # Hair care / styling.
        if re.search(r"\b(?:hs|hd|ht)[\s\-]?\d+", low):
            return "dyson_hair"

        if any(word in low for word in (
            "airwrap",
            "airstrait",
            "supersonic",
            "coanda",
            "hair dryer",
            "styler",
            "стайлер",
            "фен",
        )):
            return "dyson_hair"

        # Vacuum / floor care.
        if re.search(
            r"\b(?:v6|v7|v8|v10|v11|v12|v15|gen5|outsize|detect)\b",
            low,
        ):
            return "dyson_vacuum"

        if any(word in low for word in (
            "vacuum",
            "пылесос",
            "washg1",
            "wash g1",
            "submarine",
            "omni-glide",
            "micro 1.5kg",
        )):
            return "dyson_vacuum"

        return "other"

    if low.startswith(("xiaomi ", "redmi ", "poco ")):
        return "xiaomi_redmi"

    if low.startswith((
        "gopro ",
        "hero ",
        "insta360 ",
        "insta 360 ",
    )):
        return "gopro_insta360"

    if low.startswith((
        "meta ",
        "ray-ban ",
        "ray ban ",
        "oakley ",
    )):
        return "meta_glasses"

    if low.startswith("nintendo "):
        return "nintendo"

    if low.startswith((
        "marshall ",
        "harman kardon ",
        "hyperx ",
        "jbl ",
    )):
        return "audio"

    if low.startswith((
        "kodak ",
        "fujifilm ",
        "instax ",
    )):
        return "photo"

    if low.startswith((
        "apple tv ",
        "beats ",
    )):
        return "apple_other"

    if low.startswith("plaud "):
        return "plaud"

    return "other"


def group_catalog_lines(lines):
    groups = {key: [] for key in CATALOG_GROUP_ORDER}

    for line in lines or []:
        key = detect_catalog_group(line)
        groups.setdefault(key, []).append(line)

    return [
        (key, groups[key])
        for key in CATALOG_GROUP_ORDER
        if groups.get(key)
    ]

