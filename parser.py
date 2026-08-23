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

        # Non-iPhone products become REAL automatically discovered blocks.
        catalog_group = detect_catalog_group(line)
        auto_key = AUTO_GROUP_TO_BLOCK.get(catalog_group)

        if auto_key:
            blocks.setdefault(auto_key, []).append(line)
        else:
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


# These are real blocks, not temporary submessages of "Остальное".
# They are discovered automatically from the supplier price and then
# persisted in state, so the user can enable/disable/reorder them.
AUTO_BLOCK_TITLES = {
    "auto_dyson_hair": "📦 Dyson — фены и стайлеры",
    "auto_dyson_vacuum": "📦 Dyson — пылесосы",
    "auto_xiaomi_redmi": "📦 Xiaomi / Redmi / Poco",
    "auto_gopro_insta360": "📦 GoPro / Insta360",
    "auto_meta_glasses": "📦 Meta / Ray-Ban / Oakley",
    "auto_nintendo": "📦 Nintendo",
    "auto_audio": "📦 Аудио — Marshall / Harman Kardon / HyperX",
    "auto_photo": "📦 Фото — Kodak / Fujifilm",
    "auto_apple_other": "📦 Apple TV / Beats",
    "auto_plaud": "📦 Plaud",
}

AUTO_BLOCK_NAV_LABELS = {
    "auto_dyson_hair": "Dyson фены",
    "auto_dyson_vacuum": "Dyson пылесосы",
    "auto_xiaomi_redmi": "Xiaomi / Redmi",
    "auto_gopro_insta360": "GoPro / Insta360",
    "auto_meta_glasses": "Meta / Ray-Ban",
    "auto_nintendo": "Nintendo",
    "auto_audio": "Аудио",
    "auto_photo": "Фото",
    "auto_apple_other": "Apple TV / Beats",
    "auto_plaud": "Plaud",
}

AUTO_GROUP_TO_BLOCK = {
    "dyson_hair": "auto_dyson_hair",
    "dyson_vacuum": "auto_dyson_vacuum",
    "xiaomi_redmi": "auto_xiaomi_redmi",
    "gopro_insta360": "auto_gopro_insta360",
    "meta_glasses": "auto_meta_glasses",
    "nintendo": "auto_nintendo",
    "audio": "auto_audio",
    "photo": "auto_photo",
    "apple_other": "auto_apple_other",
    "plaud": "auto_plaud",
}


AUTO_SUBCLASS_TITLES = {
    "auto_dyson_hair": {
        # Dyson model subclasses are generated dynamically from the
        # actual product code (HT-01, HS-08, HS-09, HD-16, ...).
        "other": "Другие Dyson",
    },
    "auto_dyson_vacuum": {
        "v_series": "Dyson V-Series",
        "gen5_outsize": "Gen5 / Outsize",
        "wash_floor": "Wash / Submarine",
        "other": "Другие пылесосы Dyson",
    },
    "auto_xiaomi_redmi": {
        # Xiaomi/Redmi/Poco subclasses are generated dynamically from the
        # actual model name, e.g. Xiaomi 14T Pro / Redmi Note 14 Pro / Poco X7 Pro.
        "other": "Другие Xiaomi / Redmi / Poco",
    },
    "auto_gopro_insta360": {
        "gopro": "GoPro / Hero",
        "insta_x": "Insta360 X-Series",
        "insta_luna": "Insta360 Luna",
        "insta_other": "Другие Insta360",
        "other": "Другое",
    },
    "auto_meta_glasses": {
        "rayban": "Meta Ray-Ban",
        "oakley": "Meta Oakley",
        "other": "Другие Meta",
    },
    "auto_nintendo": {
        "switch_lite": "Nintendo Switch Lite",
        "switch_oled": "Nintendo Switch OLED",
        "switch": "Nintendo Switch",
        "other": "Другое Nintendo",
    },
    "auto_audio": {
        "marshall": "Marshall",
        "harman": "Harman Kardon",
        "hyperx": "HyperX",
        "jbl": "JBL",
        "other": "Другое аудио",
    },
    "auto_photo": {
        "kodak_camera": "Kodak — камеры",
        "kodak_film": "Kodak — плёнка / расходники",
        "fujifilm": "Fujifilm / Instax",
        "other": "Другое фото",
    },
    "auto_apple_other": {
        "apple_tv": "Apple TV",
        "beats_buds": "Beats — наушники",
        "beats_other": "Beats — другое",
        "other": "Другое Apple",
    },
    "auto_plaud": {
        "note": "Plaud Note",
        "other": "Другие Plaud",
    },
}

AUTO_SUBCLASS_ORDER = {
    key: tuple(value.keys())
    for key, value in AUTO_SUBCLASS_TITLES.items()
}


def detect_auto_subclass(block_key: str, line: str) -> str:
    txt = norm(line)
    low = txt.casefold()

    if block_key == "auto_dyson_hair":
        # Exact model code, normalized to e.g. HS-08 / HD-16 / HT-01.
        match = re.search(
            r"\b(?P<family>hs|hd|ht)[\s\-]?(?P<number>\d{1,2})\b",
            low,
            re.I,
        )

        if match:
            family = match.group("family").upper()
            number = match.group("number").zfill(2)
            return f"model:{family}-{number}"

        # If supplier omitted the code, use a readable generic subclass.
        if "airstrait" in low:
            return "name:Airstrait"
        if any(x in low for x in ("airwrap", "coanda")):
            return "name:Airwrap / Coanda"
        if "supersonic" in low:
            return "name:Supersonic"
        return "other"

    if block_key == "auto_dyson_vacuum":
        if re.search(r"\bv(?:6|7|8|10|11|12|15)\b", low):
            return "v_series"
        if any(x in low for x in ("gen5", "outsize")):
            return "gen5_outsize"
        if any(x in low for x in ("washg1", "wash g1", "submarine")):
            return "wash_floor"
        return "other"

    if block_key == "auto_xiaomi_redmi":
        # Dynamic exact-model grouping.
        # Examples:
        # Xiaomi 14T Pro 12/512 Black -> Xiaomi 14T Pro
        # Redmi Note 14 Pro 8/256 -> Redmi Note 14 Pro
        # Poco X7 Pro 12/512 Yellow -> Poco X7 Pro
        txt_original = norm(line)

        brand_match = re.match(r"^(Xiaomi|Redmi|Poco)\b\s*(.*)$", txt_original, re.I)
        if not brand_match:
            return "other"

        brand = brand_match.group(1).title()
        tail = (brand_match.group(2) or "").strip()

        stop_patterns = [
            r"\b\d+\s*/\s*\d+\b",      # 12/256
            r"\b\d+(?:GB|Tb|TB)\b",      # 256GB / 1TB
            r"\bRAM\b",
            r"\bROM\b",
            r"\(",                        # bracketed extras
            r"🇦🇪|🇺🇸|🇪🇺|🇯🇵|🇨🇳|🇮🇳|🇬🇧|🇭🇰|🇰🇷",
            r"\s-\s",                    # explicit price delimiter side
        ]

        cut_positions = []
        for pat in stop_patterns:
            m = re.search(pat, tail, re.I)
            if m:
                cut_positions.append(m.start())

        if cut_positions:
            tail = tail[:min(cut_positions)].strip()

        tokens = [t for t in re.split(r"\s+", tail) if t]
        if not tokens:
            return f"brand:{brand}"

        model_tokens = []
        allowed_simple = {
            "note", "turbo", "pro", "pro+", "plus", "ultra", "lite", "max",
            "pad", "tab", "watch", "band", "buds", "air", "civi", "mix", "flip",
            "fold", "se", "gt"
        }
        stop_words = {
            "black","white","blue","green","purple","pink","yellow","silver","gold",
            "gray","grey","midnight","lavender","coral","red","orange","graphite",
            "global","cn","eu","version"
        }

        for tok in tokens:
            t = tok.strip(",")
            tl = t.casefold()

            if model_tokens and tl in stop_words:
                break

            keep = False
            if re.match(r"^[A-Za-z]?\d+[A-Za-z+\-]*$", t):
                keep = True                    # 14T, X7, F6, 13C, 15T
            elif re.match(r"^[A-Za-z]+\d+[A-Za-z+\-]*$", t):
                keep = True                    # Note14, X6Pro
            elif tl in allowed_simple:
                keep = True                    # Pro, Ultra, Note, Turbo...
            elif re.match(r"^\d+[A-Za-z+\-]*$", t):
                keep = True                    # 14, 7

            if keep:
                model_tokens.append(t)
                continue

            if model_tokens:
                break

            # If the first post-brand token is a short word, allow it to start the model.
            if re.match(r"^[A-Za-z]{1,12}$", t):
                model_tokens.append(t)
            else:
                break

        if not model_tokens:
            return f"brand:{brand}"

        model = " ".join(model_tokens)
        return f"model:{brand} {model}"

    if block_key == "auto_gopro_insta360":
        if low.startswith(("gopro ", "hero ")):
            return "gopro"
        if low.startswith(("insta360 ", "insta 360 ")):
            if re.search(r"\bx\d*\b", low) or any(
                x in low for x in (" x4", " x5", " x6")
            ):
                return "insta_x"
            if "luna" in low:
                return "insta_luna"
            return "insta_other"
        return "other"

    if block_key == "auto_meta_glasses":
        if "oakley" in low:
            return "oakley"
        if "ray-ban" in low or "ray ban" in low:
            return "rayban"
        return "other"

    if block_key == "auto_nintendo":
        if "switch lite" in low:
            return "switch_lite"
        if "switch oled" in low:
            return "switch_oled"
        if "switch" in low:
            return "switch"
        return "other"

    if block_key == "auto_audio":
        if low.startswith("marshall "):
            return "marshall"
        if low.startswith("harman kardon "):
            return "harman"
        if low.startswith("hyperx "):
            return "hyperx"
        if low.startswith("jbl "):
            return "jbl"
        return "other"

    if block_key == "auto_photo":
        if low.startswith(("fujifilm ", "instax ")):
            return "fujifilm"

        if low.startswith("kodak "):
            # Film / cartridges / paper / accessories.
            if any(x in low for x in (
                "funsaver",
                "kodacolor",
                "colorplus",
                "сolorplus",
                "cartidge",
                "cartridge",
                "sheets",
                "film",
                "microSD".casefold(),
            )):
                return "kodak_film"
            return "kodak_camera"

        return "other"

    if block_key == "auto_apple_other":
        if low.startswith("apple tv "):
            return "apple_tv"
        if low.startswith("beats "):
            if any(x in low for x in (
                "buds",
                "powerbeats",
                "solo",
                "studio",
                "fit",
            )):
                return "beats_buds"
            return "beats_other"
        return "other"

    if block_key == "auto_plaud":
        if "note" in low:
            return "note"
        return "other"

    return "other"


def _dyson_subclass_sort_key(key: str):
    """
    Natural Dyson order:
    HT-01, HT-02...
    HS-05, HS-07, HS-08, HS-09...
    HD-07, HD-08, HD-15...
    Then name-based and "other".
    """
    family_order = {
        "HT": 0,
        "HS": 1,
        "HD": 2,
    }

    if key.startswith("model:"):
        code = key.split(":", 1)[1]
        m = re.match(r"^(HT|HS|HD)-(\d+)$", code, re.I)

        if m:
            family = m.group(1).upper()
            number = int(m.group(2))
            return (0, family_order.get(family, 99), number, code)

    if key.startswith("name:"):
        return (1, 0, 0, key.casefold())

    if key == "other":
        return (9, 0, 0, key)

    return (8, 0, 0, key.casefold())


def _xiaomi_subclass_sort_key(key: str):
    brand_order = {
        "Xiaomi": 0,
        "Redmi": 1,
        "Poco": 2,
    }

    if key.startswith("model:"):
        title = key.split(":", 1)[1]
    elif key.startswith("brand:"):
        title = key.split(":", 1)[1]
    elif key == "other":
        return (9, 0, [(1, "zzz")])
    else:
        title = key

    brand = title.split(" ", 1)[0]
    rest = title[len(brand):].strip() if title.startswith(brand) else title
    parts = re.findall(r"\d+|\D+", rest)
    natural = []
    for part in parts:
        if part.isdigit():
            natural.append((0, int(part)))
        else:
            natural.append((1, part.casefold()))

    prefix_rank = 1 if key.startswith("brand:") else 0
    return (0, brand_order.get(brand, 99), prefix_rank, natural)


def group_auto_block_lines(block_key: str, lines):
    """
    Returns [(subclass_title, rows), ...].

    Dyson hair is special: subclasses are generated automatically by the
    exact supplier model code, e.g. HS-08 / HS-09 / HD-16 / HT-01.

    Xiaomi / Redmi / Poco are also special: subclasses are generated by the
    exact detected model name, e.g. Xiaomi 14T Pro / Redmi Note 14 Pro / Poco X7 Pro.
    """
    if block_key == "auto_dyson_hair":
        groups = {}

        for line in lines or []:
            subclass = detect_auto_subclass(block_key, line)
            groups.setdefault(subclass, []).append(line)

        result = []

        for subclass in sorted(groups, key=_dyson_subclass_sort_key):
            rows = groups[subclass]

            if subclass.startswith("model:"):
                title = subclass.split(":", 1)[1]
            elif subclass.startswith("name:"):
                title = subclass.split(":", 1)[1]
            else:
                title = AUTO_SUBCLASS_TITLES["auto_dyson_hair"].get(
                    subclass,
                    "Другие Dyson",
                )

            if rows:
                result.append((title, rows))

        return result

    if block_key == "auto_xiaomi_redmi":
        groups = {}

        for line in lines or []:
            subclass = detect_auto_subclass(block_key, line)
            groups.setdefault(subclass, []).append(line)

        result = []

        for subclass in sorted(groups, key=_xiaomi_subclass_sort_key):
            rows = groups[subclass]

            if subclass.startswith("model:"):
                title = subclass.split(":", 1)[1]
            elif subclass.startswith("brand:"):
                title = subclass.split(":", 1)[1]
            else:
                title = AUTO_SUBCLASS_TITLES["auto_xiaomi_redmi"].get(
                    subclass,
                    "Другие Xiaomi / Redmi / Poco",
                )

            if rows:
                result.append((title, rows))

        return result

    titles = AUTO_SUBCLASS_TITLES.get(block_key)

    if not titles:
        return []

    order = AUTO_SUBCLASS_ORDER.get(block_key, tuple(titles))
    groups = {key: [] for key in order}

    for line in lines or []:
        subclass = detect_auto_subclass(block_key, line)

        if subclass not in groups:
            subclass = "other"

        groups.setdefault(subclass, []).append(line)

    result = []

    for subclass in order:
        rows = groups.get(subclass) or []

        if rows:
            result.append((
                titles.get(subclass, subclass),
                rows,
            ))

    return result
