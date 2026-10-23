"""Vidéos explicatives avec voix off (page /videos/ et espace client), filmées sur le vrai site.
Scénario, textes et voix : config/videos.yaml. Espace client simulé (Supabase intercepté, aucun compte réel, aucun réseau).
Voix : voix neuronale française de Microsoft (edge-tts, gratuite). Sous-titres incrustés, musique du film (static/film.mp3) en fond.
Horloge virtuelle (comme scripts/film_video.py) : chaque image est calculée puis capturée, aucune saccade.
Usage : python scripts/videos_voix.py out/site out/videos [--email fichier.html] [--seul id] [--sans-voix] [--chrome chemin]
  --sans-voix : durées estimées et vidéo muette (essai local quand la voix est inaccessible)."""
import argparse
import asyncio
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import async_playwright

RACINE = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((RACINE / "config" / "videos.yaml").read_text(encoding="utf-8"))
W, H, FPS = 1920, 1080, 30
VX, VY, VW, VH, BARRE = 160, 30, 1600, 820, 46     # fenêtre du navigateur dans l'image (pixels de la vidéo)
ZOOM = 1.25                                          # site affiché à 125 % : lisible même sur téléphone
CW, CH = round(VW / ZOOM), round(VH / ZOOM)          # la même fenêtre en pixels de la page
FONDU, ENTREE, ECART, FIN = 0.35, 0.5, 0.35, 0.9   # s : fondu entre pages, avant la 1re phrase, entre phrases, après la dernière
NUIT, IVOIRE, CITRON = (16, 28, 44), (245, 243, 237), (184, 243, 107)
PORT = 8791

HORLOGE = """(()=>{let vt=0,id=1;const q=[],raf=[],t0=Date.now(),seen=new Map();
Date.now=()=>t0+vt;performance.now=()=>vt;
window.setTimeout=(f,ms,...a)=>{const i=id++;q.push({i,t:vt+(+ms||0),f,a});return i};
window.clearTimeout=i=>{const k=q.findIndex(x=>x.i===i);if(k>=0)q.splice(k,1)};
window.setInterval=(f,ms,...a)=>{const i=id++;const r=()=>{q.push({i,t:vt+Math.max(+ms||0,16),f:()=>{r();f(...a)},a:[]})};r();return i};
window.clearInterval=window.clearTimeout;
window.requestAnimationFrame=f=>{raf.push(f);return id++};
window.__step=dt=>{const end=vt+dt;for(;;){q.sort((a,b)=>a.t-b.t);if(!q.length||q[0].t>end)break;const x=q.shift();vt=Math.max(vt,x.t);typeof x.f=='function'&&x.f(...x.a)}
 vt=end;raf.splice(0).forEach(f=>f(vt));
 document.getAnimations().forEach(an=>{if(!seen.has(an))seen.set(an,vt);an.pause();an.currentTime=vt-seen.get(an)})};})();"""

# Curseur, cercle de clic et cadres de mise en valeur, dessinés dans la page à chaque image.
DECOR = """(()=>{const css=`html{scroll-behavior:auto!important}.ra-btn,.ra-panel,#promo,.promo-bandeau,.a-promo{display:none!important}
#vv-c{position:fixed;z-index:2147483647;left:0;top:0;width:30px;height:30px;pointer-events:none;filter:drop-shadow(0 3px 6px rgba(0,0,0,.35))}
#vv-o{position:fixed;z-index:2147483646;width:46px;height:46px;margin:-23px 0 0 -23px;border-radius:50%;background:rgba(184,243,107,.55);pointer-events:none;opacity:0}
.vv-r{position:fixed;z-index:2147483645;border:3px solid #B8F36B;border-radius:16px;pointer-events:none;box-shadow:0 0 0 6px rgba(184,243,107,.28)}`;
const s=document.createElement('style');s.textContent=css;document.head.appendChild(s);
const c=document.createElement('div');c.id='vv-c';c.innerHTML='<svg viewBox="0 0 24 24" width="30" height="30"><path d="M4 2l15 11.2-6.6 1.1 3.9 7.4-3 1.5-3.8-7.5L4 20.6z" fill="#fff" stroke="#101C2C" stroke-width="1.6" stroke-linejoin="round"/></svg>';
const o=document.createElement('div');o.id='vv-o';document.body.append(c,o);
window.__vv=(e)=>{c.style.transform=`translate(${e.x-4}px,${e.y-2}px)`;c.style.display=e.cur?'block':'none';
 o.style.left=e.x+'px';o.style.top=e.y+'px';o.style.opacity=e.clic;o.style.transform=`scale(${1.6-e.clic})`;
 document.querySelectorAll('.vv-r').forEach(r=>r.remove());
 for(const r of e.anneaux){const el=document.querySelector(r.sel);if(!el)continue;const b=el.getBoundingClientRect();
  const d=document.createElement('div');d.className='vv-r';Object.assign(d.style,{left:(b.left-8)+'px',top:(b.top-8)+'px',width:(b.width+16)+'px',height:(b.height+16)+'px',opacity:r.a});document.body.appendChild(d)}};})();"""


def lisse(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def police(taille, graisse=600):
    """Inter : fichiers du système (paquet fonts-inter) si présents, sinon la police du site convertie."""
    nom = {500: "Inter-Medium.otf", 600: "Inter-SemiBold.otf"}.get(graisse, "Inter-SemiBold.otf")
    systeme = Path("/usr/share/fonts/opentype/inter") / nom
    if systeme.exists():
        return ImageFont.truetype(str(systeme), taille)
    f = ImageFont.truetype(str(POLICE), taille)
    try:
        f.set_variation_by_axes([graisse])
    except Exception:
        pass
    return f


# ---------- voix ----------
async def voix(phrases, dossier, sans_voix):
    """Une piste par phrase ; renvoie la durée (s) de chacune."""
    durees = []
    if not sans_voix:
        import edge_tts
    for i, p in enumerate(phrases):
        f = dossier / f"p{i:03d}.mp3"
        if sans_voix:
            durees.append(round(len(p.get("dit") or p["texte"]) / 15, 2))
            continue
        for essai in range(3):
            try:
                await edge_tts.Communicate(p.get("dit") or p["texte"], CFG["voix"], rate=CFG.get("debit", "+0%")).save(str(f))
                break
            except Exception as exc:
                if essai == 2:
                    raise
                print("voix : nouvel essai après", type(exc).__name__)
                await asyncio.sleep(3)
        d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                           capture_output=True, text=True, check=True).stdout
        durees.append(float(d))
    return durees


def temps(t, debuts):
    """« 1.5 », « p2 », « p2+0.8 » → secondes depuis le début de la scène."""
    if isinstance(t, (int, float)):
        return float(t)
    m = re.fullmatch(r"p(\d+)([+-][\d.]+)?", str(t).strip())
    return debuts[int(m.group(1)) - 1] + float(m.group(2) or 0)


def chronologie(video, durees):
    """Début et fin de chaque scène et de chaque phrase (s, depuis le début de la vidéo)."""
    t, k, scenes = 0.0, 0, []
    for sc in video["scenes"]:
        debuts, x = [], ENTREE
        for _ in sc["phrases"]:
            debuts.append(x)
            x += durees[k] + ECART
            k += 1
        fin_voix = x - ECART + FIN
        actions = [dict(a, t=temps(a["t"], debuts)) for a in sc.get("actions", [])]
        fin_actions = max([a["t"] + a.get("d", 0) + 0.4 for a in actions] or [0])
        duree = max(fin_voix, fin_actions)
        scenes.append({"sc": sc, "debut": t, "duree": duree, "phrases": [t + d for d in debuts], "actions": actions})
        t += duree
    return scenes, t


# ---------- pages propres aux vidéos ----------
def pages_video(site, email):
    d = site / "_videos"
    d.mkdir(exist_ok=True)
    shutil.copy(RACINE / "scripts" / "videos" / "word-exemple.png", d / "word.png")
    if email and Path(email).exists():
        html = Path(email).read_text(encoding="utf-8")
        html = html.replace("<body", "<body style=\"background:#F1F3F5;padding:20px 0;zoom:1.3\"", 1)
        (d / "email.html").write_text(html, encoding="utf-8")
    else:
        (d / "email.html").write_text("<p style='font:20px sans-serif;padding:40px'>Aperçu d'e-mail indisponible</p>", encoding="utf-8")
    base = ("<!doctype html><meta charset=utf-8><style>@font-face{font-family:Inter;src:url(/static/fonts/inter.woff2)}"
            "html,body{margin:0;height:100%;font-family:Inter,sans-serif}")
    (d / "fin.html").write_text(base + """body{background:#101C2C;color:#F5F3ED;display:grid;place-items:center;text-align:center}
.l{display:inline-flex;align-items:center;gap:18px;font-size:54px;font-weight:800;letter-spacing:-.02em}
.l i{width:76px;height:76px;border-radius:22px;background:#F5F3ED;display:grid;place-items:center}
h1{font-size:64px;line-height:1.1;margin:44px 0 18px;font-weight:800;letter-spacing:-.03em}h1 b{color:#B8F36B}
p{font-size:28px;color:#C9CED6;margin:0 0 46px}.btn{display:inline-block;background:#B8F36B;color:#101C2C;font-weight:800;font-size:30px;padding:24px 46px;border-radius:18px}
.u{margin-top:28px;font-size:24px;color:#9AA4B2}</style>
<div><div class=l><i><svg viewBox="0 0 32 32" width=52 height=52><circle cx=16 cy=16 r=11 fill=none stroke=#101C2C stroke-width=2.6 /><circle cx=16 cy=16 r=5 fill=none stroke=#101C2C stroke-width=2.6 /><path d="M16 16 26 8" stroke=#B8F36B stroke-width=3 stroke-linecap=round /></svg></i>Radar Marchés</div>
<h1>Les marchés publics de votre métier,<br><b>avant vos concurrents.</b></h1><p>7 jours d'essai gratuit · sans engagement · prix qui baisse avec l'ancienneté</p>
<span class=btn>Essayer gratuitement</span><div class=u>radar-marches.fr</div></div>""", encoding="utf-8")
    (d / "word.html").write_text(base + """body{background:#E9ECF0;padding:34px 0 0;text-align:center}
.z{position:relative;display:inline-block;box-shadow:0 20px 50px -20px rgba(16,28,44,.45)}.z img{display:block;width:1180px}
.z-reste{position:absolute;left:3.6%;top:35%;width:39%;height:32%}.z-pieces{position:absolute;left:54%;top:50%;width:41%;height:24%}</style>
<div class=z><img src=word.png alt=""><div class=z-reste></div><div class=z-pieces></div></div>""", encoding="utf-8")


# ---------- espace client simulé ----------
def donnees_espace():
    j = date.today()
    iso = lambda n: (j + timedelta(days=n)).isoformat()
    espace = {"email": "contact@net-pro.fr", "offre": "cle", "offre_nom": "Clé en main", "statut": "active", "fin_essai": None,
              "parrainage": "https://www.radar-marches.fr/parrainage/?ami=ABCDEF", "gere_par_stripe": True,
              "droits": {"actif": True, "veille": True, "departements_max": 101, "mots_cles": True, "historique_complet": True, "rapports_mois": 20, "dossiers_mois": 2},
              "criteres": {"metier": "nettoyage", "departements": ["33"], "mots_cles": ["vitrerie"], "applique": True, "erreur": None},
              "quotas": {"rapports_utilises": 1, "rapports_max": 20, "dossiers_utilises": 0, "dossiers_max": 2}}
    a = lambda i, ref, objet, ach, lieu, dl, resume, fav=False, recu=0: {
        "id": i, "avis_id": ref, "recu": iso(recu), "objet": objet, "acheteur": ach, "lieu": lieu, "date_limite": iso(dl),
        "metier": "nettoyage", "url": "https://www.boamp.fr/", "resume": resume, "favori": fav}
    alertes = [a(1, "26-104512", "Nettoyage des locaux de la mairie et de l'école élémentaire", "Commune de Pessac", "Pessac", 21,
                 "Ménage quotidien de 2 400 m² de locaux, marché de 3 ans."),
               a(2, "26-103877", "Entretien et vitrerie des bâtiments communautaires", "Bordeaux Métropole", "Bordeaux", 6,
                 "Vitrerie semestrielle et entretien courant de 12 sites.", fav=True),
               a(3, "26-102961", "Nettoyage des locaux du centre hospitalier", "CH d'Arcachon", "Arcachon", 26,
                 "Bionettoyage des services administratifs."),
               a(4, "26-101450", "Nettoyage des gymnases et salles de sport", "Ville de Mérignac", "Mérignac", 5,
                 "Entretien de 4 équipements sportifs, 2 passages par semaine.", fav=True, recu=-3),
               a(5, "26-100212", "Entretien des locaux de la médiathèque", "Commune du Bouscat", "Le Bouscat", 16,
                 "Ménage 5 jours sur 7, vitrerie trimestrielle.", recu=-6)]
    maintenant = datetime.now(timezone.utc)
    demandes = [{"id": "11111111-aaaa", "type": "rapport", "avis_ref": "26-103877", "statut": "terminee", "message": None,
                 "cree": (maintenant - timedelta(days=1)).isoformat(), "termine": (maintenant - timedelta(hours=23)).isoformat(), "fichier": "u1/d/resultat/r.html"}]
    return espace, alertes, demandes


async def simuler_supabase(ctx):
    espace, alertes, demandes = donnees_espace()
    url = json.loads((RACINE / "config" / "comptes.json").read_text())["url"] if (RACINE / "config" / "comptes.json").exists() else ""

    def b64(o):
        import base64
        return base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
    exp = int(datetime.now().timestamp()) + 7200
    user = {"id": "u1", "aud": "authenticated", "role": "authenticated", "email": espace["email"], "user_metadata": {}, "app_metadata": {}, "created_at": "2026-10-17T00:00:00Z"}
    session = {"access_token": f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'sub': 'u1', 'exp': exp, 'role': 'authenticated', 'email': espace['email']})}.sig",
               "token_type": "bearer", "expires_in": 7200, "expires_at": exp, "refresh_token": "r1", "user": user}

    async def route(r):
        req, p = r.request, re.sub(r"^https://[^/]+", "", r.request.url).split("?")[0]
        h = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*"}
        rep = lambda s, body: r.fulfill(status=s, content_type="application/json", body=json.dumps(body), headers=h)
        if req.method == "OPTIONS":
            return await r.fulfill(status=200, headers=h)
        if p == "/auth/v1/token":
            return await rep(200, session)
        if p == "/auth/v1/user":
            return await rep(200, user)
        if p == "/rest/v1/rpc/mon_espace":
            return await rep(200, espace)
        if p == "/rest/v1/alertes":
            return await rep(200, alertes)
        if p == "/rest/v1/demandes":
            return await rep(200, demandes)
        if p in ("/rest/v1/rpc/demander", "/rest/v1/rpc/soumettre_dossier"):
            if not any(d["id"] == "22222222-bbbb" for d in demandes):
                demandes.insert(0, {"id": "22222222-bbbb", "type": "dossier", "avis_ref": "26-104512", "statut": "en_traitement", "message": None,
                                    "cree": datetime.now(timezone.utc).isoformat(), "termine": None, "fichier": None})
            return await rep(200, {"id": "22222222-bbbb", "statut": "recue"})
        if p.startswith("/storage/v1/object/"):
            return await rep(200, {"Key": p})
        return await rep(200, [] if req.method == "GET" else {})
    if url:
        await ctx.route(url.rstrip("/") + "/**", route)
    return espace


async def connecter(ctx, espace):
    pg = await ctx.new_page()
    await pg.goto(f"http://localhost:{PORT}/compte/?connexion")
    try:
        await pg.wait_for_selector("#v-connexion:not([hidden])", timeout=8000)
        await pg.fill("#c-email", espace["email"])
        await pg.fill("#c-mdp", "motdepasse-video")
        await pg.click("#f-connexion button[type=submit]")
        await pg.wait_for_selector("#v-espace:not([hidden])", timeout=8000)
    except Exception as exc:
        print("connexion simulée impossible :", type(exc).__name__)
    await pg.close()


# ---------- image finale : fenêtre de navigateur, sous-titres, fondus ----------
class Cadre:
    def __init__(self):
        self.fond = Image.new("RGB", (W, H), NUIT)
        dr = ImageDraw.Draw(self.fond)
        for r in range(900, 0, -30):     # léger halo en haut à droite
            c = tuple(int(NUIT[i] + (34 - NUIT[i]) * (1 - r / 900) * 0.6) for i in range(3))
            dr.ellipse([W - 300 - r, -260 - r, W - 300 + r, -260 + r], fill=c)
        ombre = Image.new("L", (W, H), 0)
        ImageDraw.Draw(ombre).rounded_rectangle([VX, VY + 18, VX + VW, VY + BARRE + VH + 18], 22, fill=110)
        from PIL import ImageFilter
        self.fond.paste((5, 10, 18), (0, 0), ombre.filter(ImageFilter.GaussianBlur(26)))
        ImageDraw.Draw(self.fond).rounded_rectangle([VX, VY, VX + VW - 1, VY + BARRE + VH - 1], 22, fill=(241, 243, 245))
        for i, c in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
            ImageDraw.Draw(self.fond).ellipse([VX + 22 + i * 24, VY + 17, VX + 34 + i * 24, VY + 29], fill=c)
        self.masque = Image.new("L", (VW, VH), 0)
        ImageDraw.Draw(self.masque).rounded_rectangle([0, -40, VW - 1, VH - 1], 22, fill=255)
        self.f_url, self.f_cap = police(19, 500), police(38, 600)
        self.cache = {}

    def barre(self, url):
        if url not in self.cache:
            im = self.fond.copy()
            dr = ImageDraw.Draw(im)
            dr.rounded_rectangle([VX + 420, VY + 9, VX + VW - 420, VY + 37], 14, fill=(255, 255, 255))
            dr.text((VX + VW // 2, VY + 23), url, font=self.f_url, fill=(94, 106, 124), anchor="mm")
            self.cache[url] = im
        return self.cache[url]

    def lignes(self, texte):
        mots, lignes, cur = texte.split(), [], ""
        for m in mots:
            essai = (cur + " " + m).strip()
            if self.f_cap.getlength(essai) > 1560 and cur:
                lignes.append(cur)
                cur = m
            else:
                cur = essai
        return lignes + [cur]

    def image(self, capture, url, cap, alpha_cap, noir):
        im = self.barre(url).copy()
        im.paste(Image.open(io.BytesIO(capture)).convert("RGB"), (VX, VY + BARRE), self.masque)
        if cap and alpha_cap > 0:
            calque = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dr = ImageDraw.Draw(calque)
            lig = self.lignes(cap)
            y0 = VY + BARRE + VH + (H - VY - BARRE - VH) // 2 - (len(lig) * 48) // 2 + 4
            for i, l in enumerate(lig):
                dr.text((W // 2, y0 + i * 48 + 24), l, font=self.f_cap, fill=IVOIRE + (int(255 * alpha_cap),), anchor="mm")
            im = Image.alpha_composite(im.convert("RGBA"), calque).convert("RGB")
        if noir > 0:
            im = Image.blend(im, Image.new("RGB", (W, H), NUIT), noir)
        return im


# ---------- tournage ----------
async def tourner(video, scenes, total, sortie_mp4, args, affiche_jpg):
    cadre = Cadre()
    ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                           "-i", "-", "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(sortie_mp4)],
                          stdin=subprocess.PIPE)
    base = f"http://localhost:{PORT}"
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=args.chrome)
        ctx = await b.new_context(viewport={"width": CW, "height": CH}, device_scale_factor=ZOOM, locale="fr-FR")
        espace = await simuler_supabase(ctx)
        if any(str(sc.get("page", "")).startswith("/compte") for sc in video["scenes"]):
            await connecter(ctx, espace)
        pg = await ctx.new_page()
        await pg.add_init_script(HORLOGE)
        etat = {"x": CW * 0.62, "y": CH * 0.55, "cur": False, "url": "radar-marches.fr"}

        async def pas(ms):
            await pg.evaluate(f"window.__step&&__step({ms})")

        async def ouvrir(page):
            if page.startswith("#"):
                await pg.evaluate(f"location.hash={json.dumps(page)}")
            else:
                await pg.goto(base + page)
                await pg.evaluate(DECOR)
            for _ in range(25):            # la page se charge et se remplit (hors film)
                await pas(40)
                await pg.wait_for_timeout(25)
            if "#" in page and not page.startswith("/compte") and not page.startswith("#"):
                await pg.evaluate("h=>{const e=document.querySelector(h);if(e)window.scrollTo(0,e.getBoundingClientRect().top+scrollY-70)}", "#" + page.split("#")[1])
            await pas(0)

        async def boite(cible):
            try:
                return await pg.locator(cible).first.bounding_box(timeout=1500)
            except Exception:
                print("  cible introuvable :", cible)
                return None

        n_total, k = round(total * FPS), 0
        for i, s in enumerate(scenes):
            sc = s["sc"]
            if sc.get("page"):
                await ouvrir(sc["page"])
                if not sc["page"].startswith("#"):
                    etat["cur"] = False           # nouvelle page : le curseur réapparaît à la prochaine action
                    etat["url"] = sc.get("url") or ("radar-marches.fr" + sc["page"].split("#")[0]).rstrip("/")
            elif sc.get("url"):
                etat["url"] = sc["url"]
            fondu_entree = bool(sc.get("page")) and not sc["page"].startswith("#")
            suivante = scenes[i + 1]["sc"] if i + 1 < len(scenes) else None
            fondu_sortie = suivante is None or (suivante.get("page") and not suivante["page"].startswith("#"))
            en_cours, faites = [], set()
            n = round((s["debut"] + s["duree"]) * FPS) - k
            for f in range(n):
                t = f / FPS
                # actions qui démarrent
                for j, a in enumerate(s["actions"]):
                    if j in faites or a["t"] > t:
                        continue
                    faites.add(j)
                    a = dict(a)
                    if a["a"] in ("curseur", "taper", "defiler") and a.get("cible"):
                        bx = await boite(a["cible"])
                        if not bx:
                            continue
                        a["boite"] = bx
                        if a["a"] == "curseur":
                            a["de"], a["vers"] = (etat["x"], etat["y"]), (bx["x"] + min(bx["width"] * 0.5, 160), bx["y"] + min(bx["height"] * 0.5, 40))
                            etat["cur"] = True
                            dy = 0
                            if a["vers"][1] > CH - 90 or a["vers"][1] < 90:      # hors de l'écran : on fait défiler en même temps
                                dy = a["vers"][1] - CH * 0.45
                            if dy:
                                en_cours.append({"a": "defiler", "t": t, "d": a.get("d", 1), "dy": dy, "y0": await pg.evaluate("scrollY")})
                                a["vers"] = (a["vers"][0], a["vers"][1] - dy)
                        if a["a"] == "taper":
                            await pg.locator(a["cible"]).first.focus()
                            if bx["y"] + bx["height"] > CH - 40 or bx["y"] < 60:
                                dy = bx["y"] - CH * 0.4
                                en_cours.append({"a": "defiler", "t": t, "d": 0.5, "dy": dy, "y0": await pg.evaluate("scrollY")})
                            a["de"] = (etat["x"], etat["y"])
                        if a["a"] == "defiler":
                            a["dy"] = bx["y"] - a.get("marge", 90)
                    if a["a"] == "defiler" and "dy" not in a:
                        a["y0"] = await pg.evaluate("scrollY")
                        a["dy"] = a["y"] - a["y0"]
                    elif a["a"] == "defiler":
                        a["y0"] = await pg.evaluate("scrollY")
                    if a["a"] == "clic":
                        try:
                            await pg.locator(a["cible"]).first.click(timeout=3000)
                        except Exception as exc:
                            print("  clic impossible :", a["cible"], type(exc).__name__)
                        for _ in range(12):
                            await pas(16)
                            await pg.wait_for_timeout(40)
                    elif a["a"] == "fichier":
                        await pg.locator(a["cible"]).first.set_input_files({"name": a["nom"], "mimeType": "application/pdf", "buffer": b"%PDF-1.4"})
                    elif a["a"] == "cacher":
                        await pg.evaluate("s=>{const e=document.querySelector(s);if(e)e.hidden=true}", a["cible"])
                    en_cours.append(a)
                # actions en cours
                clic, anneaux = 0.0, []
                for a in en_cours:
                    u = (t - a["t"]) / max(a.get("d", 0.4), 0.01)
                    if a["a"] == "defiler" and u <= 1.05:
                        await pg.evaluate(f"window.scrollTo(0,{a['y0'] + a['dy'] * lisse(u)})")
                    elif a["a"] == "curseur" and u <= 1.05:
                        e = lisse(u)
                        etat["x"] = a["de"][0] + (a["vers"][0] - a["de"][0]) * e
                        etat["y"] = a["de"][1] + (a["vers"][1] - a["de"][1]) * e - 60 * (e * (1 - e))   # petite courbe naturelle
                    elif a["a"] == "clic" and t - a["t"] < 0.45:
                        clic = 1 - (t - a["t"]) / 0.45
                    elif a["a"] == "taper" and u <= 1.05:
                        nb = round(len(a["texte"]) * min(1, max(0, u)))
                        await pg.evaluate("([s,v])=>{const e=document.querySelector(s);if(e&&e.value!==v){e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}))}}",
                                          [a["cible"], a["texte"][:nb]])
                        if u >= 1 and not a.get("fini"):
                            a["fini"] = True
                            await pg.evaluate("s=>{const e=document.querySelector(s);e&&e.dispatchEvent(new Event('change',{bubbles:true}))}", a["cible"])
                    elif a["a"] == "surligner" and 0 <= u <= 1:
                        dd = a.get("d", 2)
                        al = min(1, (t - a["t"]) / 0.3, (a["t"] + dd - t) / 0.3)
                        anneaux.append({"sel": a["cible"], "a": round(max(0, al), 3)})
                await pg.evaluate("e=>window.__vv&&__vv(e)", {"x": etat["x"], "y": etat["y"], "cur": etat["cur"], "clic": clic, "anneaux": anneaux})
                capture = await pg.screenshot(type="jpeg", quality=92)
                # sous-titre : la phrase en cours (fondu court)
                tv, cap, al = s["debut"] + t, None, 0.0
                for pi, debut in enumerate(s["phrases"]):
                    fin = s["phrases"][pi + 1] if pi + 1 < len(s["phrases"]) else s["debut"] + s["duree"]
                    if debut - 0.15 <= tv < fin:
                        cap = sc["phrases"][pi]["texte"]
                        al = min(1, (tv - debut + 0.15) / 0.2, (fin - tv) / 0.12)
                noir = 0.0
                if fondu_entree and t < FONDU:
                    noir = 1 - lisse(t / FONDU)
                if fondu_sortie and t > s["duree"] - FONDU:
                    noir = max(noir, lisse((t - s["duree"] + FONDU) / FONDU))
                if i == 0 and t < FONDU:
                    noir = 1 - lisse(t / FONDU)
                im = cadre.image(capture, etat["url"], cap, max(0, al), noir)
                ff.stdin.write(im.tobytes())
                if video.get("affiche") == i + 1 and f == round(min(s["duree"] - 0.6, 2.2) * FPS):
                    im.save(affiche_jpg, quality=86)
                await pas(1000 / FPS)
                k += 1
                if args.apercu and k % (FPS * 2) == 0:
                    im.resize((960, 540)).save(Path(args.apercu) / f"{video['id']}-{k // FPS:03d}.jpg", quality=80)
        await b.close()
    ff.stdin.close()
    ff.wait()
    print(f"  {k} images ({k / FPS:.1f} s)")


def mixer(video_muette, pistes, debuts, total, sortie, sans_voix):
    musique = RACINE / "static" / "film.mp3"
    entrees, filtres = ["-i", str(video_muette)], []
    if not sans_voix:
        for i, (piste, d) in enumerate(zip(pistes, debuts)):
            entrees += ["-i", str(piste)]
            filtres.append(f"[{i + 1}:a]adelay={int(d * 1000)}:all=1,volume=1.0[v{i}]")
    m = len(pistes) + 1 if not sans_voix else 1
    # boucle de 7 mesures (16,15 s à 104/min : la grille retombe sur son 1er accord), très bas sous la voix
    boucle = Path(video_muette).with_name("boucle.wav")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(musique), "-t", "16.154", str(boucle)], check=True)
    entrees += ["-stream_loop", "-1", "-i", str(boucle)]
    filtres.append(f"[{m}:a]atrim=0:{total:.2f},volume=0.09,afade=t=in:d=1,afade=t=out:st={max(0, total - 2.5):.2f}:d=2.5[mu]")
    voix = "".join(f"[v{i}]" for i in range(len(pistes))) if not sans_voix else ""
    n = (len(pistes) if not sans_voix else 0) + 1
    filtres.append(f"{voix}[mu]amix=inputs={n}:normalize=0:duration=longest,atrim=0:{total:.2f}[a]")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *entrees, "-filter_complex", ";".join(filtres),
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                    "-t", f"{total:.2f}", str(sortie)], check=True)


async def main():
    global POLICE
    ap = argparse.ArgumentParser()
    ap.add_argument("site")
    ap.add_argument("sortie")
    ap.add_argument("--email")
    ap.add_argument("--seul")
    ap.add_argument("--sans-voix", action="store_true")
    ap.add_argument("--chrome")
    ap.add_argument("--apercu", help="dossier où poser une image toutes les 2 s (contrôle rapide)")
    args = ap.parse_args()
    site, sortie = Path(args.site), Path(args.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    if args.apercu:
        Path(args.apercu).mkdir(parents=True, exist_ok=True)
    pages_video(site, args.email)
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(site)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(1)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        woff = RACINE / "static" / "fonts" / "inter.woff2"
        if woff.exists():
            from fontTools.ttLib import TTFont
            POLICE = tmp / "inter.ttf"
            f = TTFont(str(woff))
            f.flavor = None
            f.save(str(POLICE))
        resume = []
        for video in CFG["videos"]:
            if args.seul and video["id"] != args.seul:
                continue
            d = tmp / video["id"]
            d.mkdir()
            phrases = [p for sc in video["scenes"] for p in sc["phrases"]]
            durees = await voix(phrases, d, args.sans_voix)
            scenes, total = chronologie(video, durees)
            muette = d / "muette.mp4"
            await tourner(video, scenes, total, muette, args, sortie / f"{video['id']}-affiche.jpg")
            debuts = [x for s in scenes for x in s["phrases"]]
            mixer(muette, [d / f"p{i:03d}.mp3" for i in range(len(phrases))], debuts, total, sortie / f"{video['id']}.mp4", args.sans_voix)
            mo = (sortie / f"{video['id']}.mp4").stat().st_size // 1024
            resume.append(f"{video['id']} : {total:.1f} s, {mo} Ko")
    srv.kill()
    print("\n".join(resume))


POLICE = None
asyncio.run(main())
