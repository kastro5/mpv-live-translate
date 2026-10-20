"""Language codes. Scribe reports ISO 639-3 ("jpn"); filenames and mpv use ISO 639-1 ("ja") where one exists."""

# ISO 639-3 -> (ISO 639-1, English name)
LANGS = {
    "ara": ("ar", "Arabic"),
    "cmn": ("zh", "Chinese"),
    "deu": ("de", "German"),
    "eng": ("en", "English"),
    "fra": ("fr", "French"),
    "hin": ("hi", "Hindi"),
    "ind": ("id", "Indonesian"),
    "ita": ("it", "Italian"),
    "jpn": ("ja", "Japanese"),
    "kor": ("ko", "Korean"),
    "nld": ("nl", "Dutch"),
    "pol": ("pl", "Polish"),
    "por": ("pt", "Portuguese"),
    "rus": ("ru", "Russian"),
    "spa": ("es", "Spanish"),
    "tha": ("th", "Thai"),
    "tur": ("tr", "Turkish"),
    "ukr": ("uk", "Ukrainian"),
    "vie": ("vi", "Vietnamese"),
    "yue": ("zh", "Cantonese"),
    "zho": ("zh", "Chinese"),
}
_ISO1_TO_3 = {iso1: iso3 for iso3, (iso1, _) in LANGS.items() if iso3 not in ("cmn", "yue")}


def iso1(code):
    """'jpn' or 'ja' -> 'ja'; unknown codes pass through."""
    code = code.lower()
    return LANGS[code][0] if code in LANGS else code


def iso3(code):
    """'ja' or 'jpn' -> 'jpn'; unknown codes pass through."""
    code = code.lower()
    return _ISO1_TO_3.get(code, code)


def name(code):
    """'ja' / 'jpn' -> 'Japanese'; unknown codes pass through."""
    entry = LANGS.get(iso3(code))
    return entry[1] if entry else code
