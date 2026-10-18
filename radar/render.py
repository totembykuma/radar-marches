from datetime import date, datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]


def date_fr(d=None):
    d = d or date.today()
    return f"{d.day} {MOIS[d.month - 1]} {d.year}"


def date_limite_fr(s):
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        return s
    txt = date_fr(dt.date())
    if len(s) > 10 and (dt.hour or dt.minute):
        txt += f" à {dt.hour}h{dt.minute:02d}"
    return txt


env = Environment(loader=FileSystemLoader(str(ROOT / "templates")),
                  autoescape=select_autoescape(["html"]))


def phrase(s):
    """Texte tout en majuscules → phrase normale (plus lisible)."""
    s = (s or "").strip()
    return s.capitalize() if s.isupper() else s


env.filters["phrase"] = phrase


def render(name, **ctx):
    return env.get_template(name).render(**ctx)


def render_string(src, **ctx):
    return env.from_string(src).render(**ctx)


def euros(n):
    """45000 → « 45 000 € » ; 1 250 000 → « 1,25 M€ »."""
    if n is None:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " M€"
    return f"{n:,}".replace(",", "\u202f") + " €"


def date_courte(s):
    try:
        d = date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return s or ""
    return f"{MOIS[d.month - 1]} {d.year}"


env.filters["euros"] = euros
env.filters["date_courte"] = date_courte


def _grille(offre):
    from .paliers import texte_grille
    return texte_grille(offre)


def _prix_fr(prix):
    from .paliers import prix_fr
    return prix_fr(prix)


env.filters["grille"] = _grille     # « 29 € les 2 premiers mois, 19,99 € du 3e au 5e mois, puis 9,99 € à partir du 6e mois »
env.filters["prix_fr"] = _prix_fr   # 19.99 → « 19,99 € »
