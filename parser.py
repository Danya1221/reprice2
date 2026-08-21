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

# US iPhone 14/15/16 variants are eSIM-only.
# For our 15/16 blocks, 🇺🇸 is therefore eSIM.
US_MARKERS = (
    "🇺🇸",
    " usa ",
    " us ",
    " united states ",
)


def norm(text: str) -> str:
    return " ".join(
        (text or "")
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
        .split()
    )


def closed_match(text: str, closed_text: str) -> bool:
    return norm(closed_text).casefold() in norm(text).casefold()


def generation_in_line(line: str):
    txt = norm(line)

    # Supports: 15, 15 Pro, 16E, 17e, 17 Air, 17 Pro Max, etc.
    m = re.match(
        r"^(?:iphone\s*)?(12|13|14|15|16|17)(?!\d)",
        txt,
        re.I,
    )
    if m:
        return m.group(1)

    return None


def explicit_sim_type(line: str):
    """
    Returns:
      sim  -> physical SIM exists (1Sim+eSim / SIM)
      esim -> eSIM-only
      None -> not explicitly written
    """
    txt = norm(line)
    compact = re.sub(r"\s+", "", txt.casefold())

    # Must be checked first, because this string also contains 'esim'.
    if (
        "1sim+esim" in compact
        or "1sim/esim" in compact
        or "1sim+e-sim" in compact
    ):
        return "sim"

    if re.search(r"\(\s*e[\s\-]?sim\s*\)", txt, re.I):
        return "esim"

    if re.search(r"\(\s*(?:1\s*)?sim\s*\)", txt, re.I):
        return "sim"

    # Explicit text outside parentheses.
    if re.search(r"\b1\s*sim\s*\+\s*e[\s\-]?sim\b", txt, re.I):
        return "sim"

    return None


def is_us_region(line: str):
    txt = f" {norm(line).casefold()} "
    if "🇺🇸" in txt:
        return True

    return any(marker in txt for marker in US_MARKERS if marker != "🇺🇸")


def detect_sim_type(line: str, generation=None):
    """
    Strict classification.

    17 series:
      - (eSim)       -> eSIM
      - (1Sim+eSim)  -> SIM
      - if no explicit marker -> unknown (Other), no guessing.

    15/16 series:
      - explicit marker wins
      - USA / 🇺🇸 -> eSIM
      - all other regions -> SIM
    """
    explicit = explicit_sim_type(line)

    if explicit:
        return explicit

    if generation in {"15", "16"}:
        return "esim" if is_us_region(line) else "sim"

    if generation == "17":
        # We intentionally do not inherit prior line's type.
        # Every 17 product must be classified by its own marker.
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


def looks_like_product(line: str):
    txt = norm(line)

    if len(txt) < 5 or is_context_header(txt):
        return False

    # Actual product line needs a number and a price separator / price-like tail.
    if not any(ch.isdigit() for ch in txt):
        return False

    return True


def parse_full_price(text: str):
    """
    Always keeps the full supplier price.
    Filtering is only used for publication blocks.
    """
    full_lines = []
    blocks = {key: [] for key in BLOCK_KEYS}

    for raw in (text or "").splitlines():
        line = norm(raw)

        if not line:
            continue

        full_lines.append(line)

        if is_context_header(line):
            continue

        if not looks_like_product(line):
            continue

        gen = generation_in_line(line)

        if gen in {"12", "13", "14"}:
            blocks[gen].append(line)
            continue

        if gen in {"15", "16", "17"}:
            sim_type = detect_sim_type(line, generation=gen)

            if sim_type == "sim":
                blocks[f"{gen}_sim"].append(line)
            elif sim_type == "esim":
                blocks[f"{gen}_esim"].append(line)
            else:
                # Unknown 17 type should not silently leak into SIM/eSIM.
                blocks["other"].append(line)

            continue

        blocks["other"].append(line)

    return {
        "full_lines": full_lines,
        "blocks": blocks,
    }


def parse_price(text: str):
    return parse_full_price(text)["blocks"]
