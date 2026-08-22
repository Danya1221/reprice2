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
    "12": "🍏 iPhone 12",
    "13": "🍏 iPhone 13",
    "14": "🍏 iPhone 14",
    "15_sim": "🍏 iPhone 15 — SIM",
    "15_esim": "🍏 iPhone 15 — eSIM",
    "16_sim": "🍏 iPhone 16 — SIM",
    "16_esim": "🍏 iPhone 16 — eSIM",
    "17_sim": "🍏 iPhone 17 — SIM",
    "17_esim": "🍏 iPhone 17 — eSIM",
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


def iphone17_family(line: str):
    """
    Возвращает семейство для сортировки внутри блоков 17 SIM/eSIM.
    Важно проверять Pro Max раньше Pro.
    """
    txt = norm(line)

    if re.match(r"^(?:iphone\s*)?17e(?!\d)", txt, re.I):
        return "17e"

    if re.match(r"^(?:iphone\s*)?17\s+pro\s+max\b", txt, re.I):
        return "17_pro_max"

    if re.match(r"^(?:iphone\s*)?17\s+pro\b", txt, re.I):
        return "17_pro"

    if re.match(r"^(?:iphone\s*)?17(?!\d)", txt, re.I):
        return "17"

    return None


def looks_like_product(line: str):
    txt = norm(line)

    if len(txt) < 5 or is_context_header(txt):
        return False

    return any(ch.isdigit() for ch in txt)


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


IPHONE17_SECTION_TITLES = {
    "17e": "17e",
    "17": "17",
    "17_pro": "17 Pro",
    "17_pro_max": "17 Pro Max",
}


def group_17_lines(lines):
    """
    Стабильно группирует строки 17-й серии:
    17e -> 17 -> 17 Pro -> 17 Pro Max.
    Возвращает список кортежей (section_key, rows).
    """
    groups = {
        "17e": [],
        "17": [],
        "17_pro": [],
        "17_pro_max": [],
    }
    other = []

    for line in lines or []:
        family = iphone17_family(line)
        if family in groups:
            groups[family].append(line)
        else:
            other.append(line)

    result = []

    for key in ("17e", "17", "17_pro", "17_pro_max"):
        if groups[key]:
            result.append((key, groups[key]))

    if other:
        result.append(("other", other))

    return result
