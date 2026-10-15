import re
import unicodedata


def norm(text):
    """minuscules, sans accents, espaces simplifiés."""
    if not text:
        return ""
    if isinstance(text, (list, tuple)):
        text = " ".join(str(t) for t in text)
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("œ", "oe").replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", norm(text)).strip("-")
