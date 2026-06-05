from __future__ import annotations

SPECIAL_CHARS: dict[str, str] = {
    "{D}": "\u00b0",
    "{<<}": "\u00ab",
    "{>>}": "\u00bb",
    "{u}": "\u00b5",
    "{E}": "\u20ac",
    "{Y}": "\u00a5",
    "{P}": "\u00a3",
    "{c}": "\u00a2",
    "{^1}": "\u00b9",
    "{^2}": "\u00b2",
    "{^3}": "\u00b3",
    "{1_4}": "\u00bc",
    "{1_2}": "\u00bd",
    "{3_4}": "\u00be",
    "{+-}": "\u00b1",
    "{x}": "\u00d7",
    "{..}": "\u00f7",
}

HTML_ENTITIES: dict[str, str] = {
    "&deg;": "\u00b0",
    "&laquo;": "\u00ab",
    "&raquo;": "\u00bb",
    "&micro;": "\u00b5",
    "&euro;": "\u20ac",
    "&yen;": "\u00a5",
    "&pound;": "\u00a3",
    "&cent;": "\u00a2",
    "&sup1;": "\u00b9",
    "&sup2;": "\u00b2",
    "&sup3;": "\u00b3",
    "&frac14;": "\u00bc",
    "&frac12;": "\u00bd",
    "&frac34;": "\u00be",
    "&plusmn;": "\u00b1",
    "&times;": "\u00d7",
    "&divide;": "\u00f7",
}


def resolve_special_chars(text: str) -> str:
    if not isinstance(text, str):
        return text
    if "{" not in text and "&" not in text:
        return text
    result = text
    for code, char in SPECIAL_CHARS.items():
        if code in result:
            result = result.replace(code, char)
    for entity, char in HTML_ENTITIES.items():
        if entity in result:
            result = result.replace(entity, char)
    return result
