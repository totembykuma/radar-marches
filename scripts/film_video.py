"""Enregistre le film de l'accueil en MP4 1920x1080, 30 images/s, avec sa musique (aperçu / réseaux sociaux).
Horloge virtuelle : chaque image est calculée puis capturée, donc aucune saccade quelle que soit la machine.
Usage : python scripts/film_video.py out/site out/film.mp4  (nécessite playwright + chromium + ffmpeg)"""
import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from playwright.async_api import async_playwright

SITE, SORTIE = Path(sys.argv[1]), Path(sys.argv[2])
EXE = sys.argv[3] if len(sys.argv) > 3 else None
SCENES = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "film.yaml").read_text(encoding="utf-8"))["scenes"]
DUREE = sum(s["ms"] for s in SCENES) / 1000   # la musique dure autant que le film
FPS = 30
HORLOGE = """(()=>{let vt=0,id=1;const q=[],raf=[],t0=Date.now(),seen=new Map();
Date.now=()=>t0+vt;performance.now=()=>vt;
window.setTimeout=(f,ms,...a)=>{const i=id++;q.push({i,t:vt+(+ms||0),f,a});return i};
window.clearTimeout=i=>{const k=q.findIndex(x=>x.i===i);if(k>=0)q.splice(k,1)};
window.requestAnimationFrame=f=>{raf.push(f);return id++};
window.__step=dt=>{const end=vt+dt;for(;;){q.sort((a,b)=>a.t-b.t);if(!q.length||q[0].t>end)break;const x=q.shift();vt=Math.max(vt,x.t);typeof x.f=='function'&&x.f(...x.a)}
 vt=end;raf.splice(0).forEach(f=>f(vt));
 document.getAnimations().forEach(an=>{if(!seen.has(an))seen.set(an,vt);an.pause();an.currentTime=vt-seen.get(an)})};})();"""
CSS = """
html,body{margin:0;width:1920px;height:1080px;overflow:hidden;background:radial-gradient(1200px 700px at 85% -10%,#1f4c8f 0%,transparent 60%),linear-gradient(160deg,#0b1b33,#13294b)!important}
body>*{display:none!important}body>#film{display:block!important}
#film{position:fixed;left:160px;top:36px;width:1600px;max-width:none;margin:0}
#film .film-view{width:1600px;height:900px;border-radius:28px;box-shadow:0 40px 80px -30px rgba(0,0,0,.8),0 0 0 1px rgba(255,255,255,.08)}
#film .film-ctrl,#film .film-big,#film .film-poster{display:none!important}
#film .film-cap{margin:30px 0 0;min-height:0;text-align:center;color:#e6edf8;font-size:31px;line-height:1.35}
#film .film-cap b{color:#ffb020}
#film .film-cta{font-size:34px;padding:24px 46px;border-radius:20px}
"""


async def main():
    srv = subprocess.Popen([sys.executable, "-m", "http.server", "8790", "-d", str(SITE)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(1)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        async with async_playwright() as p:
            b = await p.chromium.launch(executable_path=EXE)
            pg = await b.new_page(viewport={"width": 1920, "height": 1080})
            await pg.add_init_script(HORLOGE)
            await pg.goto("http://localhost:8790/")
            await pg.evaluate("f=>{const x=document.getElementById('film');document.body.appendChild(x);"
                              "const s=document.createElement('style');s.textContent=f;document.head.appendChild(s);"
                              "x.querySelector('audio')?.remove();window.dispatchEvent(new Event('resize'))}", CSS)
            await pg.wait_for_timeout(800)
            await pg.evaluate("document.querySelector('#film .film-big').click();__step(0)")
            for k in range(round(DUREE * FPS)):
                await pg.screenshot(path=str(d / f"{k:05d}.jpg"), type="jpeg", quality=95)
                await pg.evaluate(f"__step({1000 / FPS})")
            await b.close()
        srv.kill()
        musique = SITE / "static" / "film.mp3"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", str(d / "%05d.jpg"), "-i", str(musique),
                        "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                        "-c:a", "aac", "-b:a", "160k", "-t", str(DUREE), "-movflags", "+faststart", str(SORTIE)], check=True)
    print(f"{round(DUREE * FPS)} images, {SORTIE} ({SORTIE.stat().st_size // 1024} Ko)")


asyncio.run(main())
