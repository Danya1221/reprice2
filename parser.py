import re

BLOCK_KEYS = (
    "15_sim", "15_esim",
    "16_sim", "16_esim",
    "17_sim", "17_esim",
)

BLOCK_TITLES = {
    "15_sim": "🍏 iPhone 15 — SIM",
    "15_esim": "🍏 iPhone 15 — eSIM",
    "16_sim": "🍏 iPhone 16 — SIM",
    "16_esim": "🍏 iPhone 16 — eSIM",
    "17_sim": "🍏 iPhone 17 — SIM",
    "17_esim": "🍏 iPhone 17 — eSIM",
}

GEN_RE = {
    "15": re.compile(r"(?<!\d)15(?!\d)", re.I),
    "16": re.compile(r"(?<!\d)16(?!\d)", re.I),
    "17": re.compile(r"(?<!\d)17(?!\d)", re.I),
}

ESIM_RE = re.compile(r"\be[\s\-]?sim\b", re.I)
SIM_RE = re.compile(r"(?<!e)\bsim\b|\bdual\s*sim\b|\bphysical\s*sim\b", re.I)

HEADERISH_RE = re.compile(
    r"^\s*(?:iphone\s*)?(15|16|17)\s*(?:series|серия)?\s*$",
    re.I,
)


def norm(text: str) -> str:
    return " ".join((text or "").replace("\u00a0", " ").split())


def closed_match(text: str, closed_text: str) -> bool:
    return norm(closed_text).casefold() in norm(text).casefold()


def detect_generation(line: str, current=None):
    m = HEADERISH_RE.match(norm(line))
    if m:
        return m.group(1)
    lowered = norm(line)
    for gen, rx in GEN_RE.items():
        if rx.search(lowered):
            return gen
    return current


def detect_sim_type(line: str, current=None):
    txt = norm(line)
    # Важно: сначала eSIM, потому что внутри слова eSIM есть "sim".
    if ESIM_RE.search(txt):
        return "esim"
    if SIM_RE.search(txt):
        return "sim"

    low = txt.casefold()
    # Поддержка заголовков разделов.
    if low in {"esim", "e-sim", "e sim"}:
        return "esim"
    if low in {"sim", "physical sim", "dual sim"}:
        return "sim"

    return current


def is_context_header(line: str):
    txt = norm(line).casefold().strip(":-—–|")
    if not txt:
        return True
    if txt in {"sim", "esim", "e-sim", "e sim", "physical sim", "dual sim"}:
        return True
    if HEADERISH_RE.match(norm(line)):
        return True
    # Общие декоративные заголовки без цифр/цен.
    if len(txt) < 35 and not any(ch.isdigit() for ch in txt):
        if "iphone" in txt or "series" in txt or "серия" in txt:
            return True
    return False


def looks_like_product(line: str):
    txt = norm(line)
    if len(txt) < 4:
        return False
    has_letter = any(ch.isalpha() for ch in txt)
    has_digit = any(ch.isdigit() for ch in txt)
    return has_letter and has_digit and not is_context_header(txt)


def parse_price(text: str):
    """
    Разбивает общий прайс на 6 стабильных блоков:
    15/16/17 × SIM/eSIM.

    Умеет понимать как строки, где SIM/eSIM и поколение указаны в самой строке,
    так и прайсы с заголовками разделов, где контекст действует на следующие строки.
    """
    result = {key: [] for key in BLOCK_KEYS}

    current_gen = None
    current_sim = None

    for raw in (text or "").splitlines():
        line = norm(raw)
        if not line:
            continue

        new_gen = detect_generation(line, current_gen)
        new_sim = detect_sim_type(line, current_sim)

        # Заголовок меняет контекст, но сам в товары не идёт.
        if is_context_header(line):
            current_gen = new_gen
            current_sim = new_sim
            continue

        gen = detect_generation(line, current_gen)
        sim_type = detect_sim_type(line, current_sim)

        if gen in {"15", "16", "17"} and sim_type in {"sim", "esim"} and looks_like_product(line):
            key = f"{gen}_{sim_type}"
            result[key].append(line)

        current_gen = gen
        current_sim = sim_type

    return result
