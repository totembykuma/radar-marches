"""Fabrique config/carte_france.json : tracés SVG des 101 départements (métropole, Corse, outre-mer en encarts).

À lancer une seule fois (données déjà dans le dépôt) : python3 scripts/carte_france.py
Source : github.com/gregoiredavid/france-geojson (contours IGN / Etalab, Licence Ouverte).
"""
import json
import math
import urllib.request
from pathlib import Path

SRC = "https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/"
OUT = Path(__file__).resolve().parent.parent / "config" / "carte_france.json"
W, H = 800, 610
K = math.cos(math.radians(46.5))
PARIS = ("75", "92", "93", "94")
OUTRE_MER = ("971", "972", "973", "974", "976")
# Encarts de la colonne de droite : (codes, titre)
ENCARTS = [(PARIS, "Paris et petite couronne")] + [((c,), None) for c in OUTRE_MER]


def charger(nom):
    with urllib.request.urlopen(SRC + nom) as r:
        return json.load(r)["features"]


def anneaux(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [p[0] for p in polys]  # contour extérieur seulement (aucun département n'a de trou visible)


def proj(lon, lat):
    return lon * K, -lat


def rdp(pts, eps):
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    n = math.hypot(dx, dy) or 1e-9
    i, dmax = 0, 0.0
    for j in range(1, len(pts) - 1):
        d = abs(dy * pts[j][0] - dx * pts[j][1] + x2 * y1 - y2 * x1) / n
        if d > dmax:
            i, dmax = j, d
    if dmax <= eps:
        return [pts[0], pts[-1]]
    return rdp(pts[:i + 1], eps)[:-1] + rdp(pts[i:], eps)


def cadre(features):
    xs, ys = [], []
    for f in features:
        for r in anneaux(f["geometry"]):
            for lon, lat in r:
                x, y = proj(lon, lat)
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def tracer(f, box, ox, oy, bw, bh, eps=0.5):
    x0, y0, x1, y1 = box
    s = min(bw / (x1 - x0), bh / (y1 - y0))
    dx, dy = ox + (bw - (x1 - x0) * s) / 2, oy + (bh - (y1 - y0) * s) / 2
    d = []
    for r in anneaux(f["geometry"]):
        pts = [((proj(*p)[0] - x0) * s + dx, (proj(*p)[1] - y0) * s + dy) for p in r]
        m = len(pts) // 2  # anneau fermé : simplifier chaque moitié séparément
        pts = rdp(pts[:m + 1], eps)[:-1] + rdp(pts[m:], eps)
        if len(pts) < 3 or (max(p[0] for p in pts) - min(p[0] for p in pts)) < 0.8:
            continue  # îlots invisibles à cette échelle
        d.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
    return "".join(d).replace(".0,", ",").replace(".0L", "L").replace(".0Z", "Z")


def main():
    metro = charger("departements-version-simplifiee.geojson")
    om = {f["properties"]["code"]: f for f in charger("departements-avec-outre-mer.geojson")
          if f["properties"]["code"] in OUTRE_MER}
    deps, encarts = {}, []
    box = cadre(metro)
    for f in metro:
        c = f["properties"]["code"]
        deps[c] = {"code": c, "nom": f["properties"]["nom"], "d": tracer(f, box, 8, 8, 612, H - 16)}
    par_code = {f["properties"]["code"]: f for f in metro} | om
    hauteur = (H - 16) / len(ENCARTS)
    for i, (codes, titre) in enumerate(ENCARTS):
        oy = 8 + i * hauteur
        feats = [par_code[c] for c in codes]
        b = cadre(feats)
        for f in feats:
            c = f["properties"]["code"]
            d = tracer(f, b, 642, oy + 16, 150, hauteur - 26, eps=0.4)
            if c in OUTRE_MER:
                deps[c] = {"code": c, "nom": f["properties"]["nom"], "d": d}
            else:
                deps[c]["zoom"] = d
        encarts.append({"x": 636, "y": round(oy, 1), "w": 160, "h": round(hauteur - 6, 1),
                        "titre": titre or par_code[codes[0]]["properties"]["nom"], **({} if titre else {"code": codes[0]})})
    data = {"viewbox": f"0 0 {W} {H}", "encarts": encarts,
            "departements": sorted(deps.values(), key=lambda d: d["code"])}
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(deps)} départements, {OUT.stat().st_size // 1024} Ko")


if __name__ == "__main__":
    main()
