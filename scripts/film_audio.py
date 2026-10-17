"""Bande son du film de l'accueil : voix off française + fond sonore original, calés scène par scène.
Voix : Piper (synthèse vocale libre, hors ligne), voix fr_FR-siwis-medium. Fond sonore : synthèse numpy (création originale).
Écrit static/film.mp3 et config/film_temps.json (durée de chaque scène et début de chaque phrase, lus par le site).
Usage : python scripts/film_audio.py VOIX.onnx    (ou --sans-voix : silence à la place de la voix, pour essayer le montage)"""
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
import yaml

RACINE = Path(__file__).resolve().parent.parent
SR = 44100
DEBUT, ENTRE, APRES, FIN_QUEUE = .7, .38, .75, 3.2   # silences : avant la 1re phrase, entre phrases, après la dernière, fin du film
rng = np.random.default_rng(11)


def voix_piper(modele):
    from piper import PiperVoice, SynthesisConfig
    v = PiperVoice.load(modele)
    cfg = SynthesisConfig(length_scale=1.06, noise_scale=.6, noise_w_scale=.75)

    def dire(texte):
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            with wave.open(f.name, "wb") as w:
                v.synthesize_wav(texte, w, syn_config=cfg)
            # mise à 44,1 kHz + traitement voix (coupe-bas, compression douce, présence)
            raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", f.name, "-af",
                                  "highpass=f=90,acompressor=threshold=-20dB:ratio=3:attack=4:release=90,"
                                  "equalizer=f=3200:t=q:w=1.2:g=2.5,equalizer=f=220:t=q:w=1:g=1.5",
                                  "-ar", str(SR), "-ac", "1", "-f", "f32le", "-"], capture_output=True, check=True).stdout
        x = np.frombuffer(raw, "<f4").astype(float)
        nz = np.flatnonzero(np.abs(x) > .01)   # retire les silences de bord de Piper
        return x[max(0, nz[0] - 400):nz[-1] + 1200] if len(nz) else x
    return dire


def voix_muette(texte):
    return np.zeros(int(SR * len(texte) / 15.5))


def hz(n):
    return 440 * 2 ** ((n - 69) / 12)


def env(n, a, r):
    t = np.arange(n) / SR
    return np.minimum(1, t / a) * np.clip((n / SR - t) / r, 0, 1)


def lowpass_fft(x, fc, q=1.0):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.fft.irfft(X / (1 + (f / fc) ** (2 * q)), len(x))


def main():
    sans_voix = "--sans-voix" in sys.argv
    dire = voix_muette if sans_voix else voix_piper(sys.argv[1])
    scenes = yaml.safe_load((RACINE / "config" / "film.yaml").read_text(encoding="utf-8"))["scenes"]

    # 1. Voix, phrase par phrase : on en déduit la durée de chaque scène
    pistes, temps, t = [], {}, 0.0
    for k, s in enumerate(scenes):
        debut, marks, pos = t, [], DEBUT
        for p in s["voix"]:
            x = dire(p)
            marks.append(round(pos * 1000))
            pistes.append((t + pos, x))
            pos += len(x) / SR + ENTRE
        dur = pos - ENTRE + (FIN_QUEUE if k == len(scenes) - 1 else APRES)
        temps[s["id"]] = {"debut": round(debut * 1000), "ms": round(dur * 1000), "marks": marks}
        t += dur
    total = t
    N = int(SR * total) + SR
    V = np.zeros(N)
    for t0, x in pistes:
        i = int(t0 * SR)
        V[i:i + len(x)] += x
    if not sans_voix:
        V *= .5 / max(1e-9, np.abs(V).max())

    # 2. Fond sonore : nappe douce, arpège discret, pulsation légère, transitions et petits accents
    L, R = np.zeros(N), np.zeros(N)

    def add(sig, t0, pan=0.0, g=1.0):
        i = int(t0 * SR)
        if i >= N:
            return
        sig = sig[:N - i] * g
        L[i:i + len(sig)] += sig * np.sqrt((1 - pan) / 2) * 1.414
        R[i:i + len(sig)] += sig * np.sqrt((1 + pan) / 2) * 1.414

    def pad(notes, t0, dur, g):
        n = int(dur * SR)
        tt = np.arange(n) / SR
        s = sum(np.sin(2 * np.pi * hz(m + d) * tt + d * 9) + .2 * np.sin(4 * np.pi * hz(m + d) * tt) for m in notes for d in (-.08, 0, .08))
        s = lowpass_fft(s * env(n, 1.2, 1.4), 1400)
        add(s, t0, -.3, g)
        add(s, t0 + .013, .3, g)

    def pluck(m, t0, g, pan):
        n = int(.6 * SR)
        tt = np.arange(n) / SR
        f = hz(m)
        add((np.sin(2 * np.pi * f * tt) + .3 * np.sin(4 * np.pi * f * tt)) * np.exp(-tt * 6) * env(n, .004, .05), t0, pan, g)

    def bell(m, t0, g, pan=0.0):
        n = int(2.4 * SR)
        tt = np.arange(n) / SR
        f = hz(m)
        s = (np.sin(2 * np.pi * f * tt) + .45 * np.sin(2 * np.pi * f * 2.76 * tt) * np.exp(-tt * 3) + .2 * np.sin(2 * np.pi * f * 5.4 * tt) * np.exp(-tt * 6)) * np.exp(-tt * 1.7)
        add(s * env(n, .002, .3), t0, pan, g)

    def thump(t0, g):
        n = int(.3 * SR)
        tt = np.arange(n) / SR
        add(np.sin(2 * np.pi * np.cumsum(48 + 70 * np.exp(-tt * 28)) / SR) * np.exp(-tt * 11), t0, 0, g)

    def tick(t0, g, pan):
        n = int(.05 * SR)
        tt = np.arange(n) / SR
        add(np.diff(rng.standard_normal(n + 1)) * np.exp(-tt * 90), t0, pan, g)

    def whoosh(t_fin, dur=1.1, g=.09):
        n = int(dur * SR)
        tt = np.arange(n) / SR
        s = lowpass_fft(rng.standard_normal(n), 900) * (tt / dur) ** 2.2 * np.clip((dur - tt) / .12, 0, 1)
        add(s, t_fin - dur + .1, -.4, g)
        add(s, t_fin - dur + .14, .4, g * .8)

    BPM = 96
    BEAT = 60 / BPM
    ACC = [(60, 64, 67, 71), (57, 60, 64, 67), (53, 57, 60, 64), (55, 59, 62, 67)]   # Do maj7, La m7, Fa maj7, Sol
    BAS = [36, 33, 29, 31]
    fin = temps["fin"]["debut"] / 1000
    gagne = temps["gagne"]["debut"] / 1000
    t, k = 0.0, 0
    while t < fin:
        a = ACC[k % 4]
        pad(list(a), t, 4 * BEAT + 1.0, .022)
        for s, idx in enumerate([0, 2, 1, 3, 2, 1, 3, 2]):
            ts = t + s * BEAT / 2
            if ts < fin:
                pluck(a[idx] + 12, ts, .05 if t < 4 else .075, -.4 if s % 2 else .4)
        if t >= 4:
            for s in range(4):
                ts = t + s * BEAT
                if ts < fin:
                    thump(ts, .16 if s in (0, 2) else .09)
                    tick(ts + BEAT / 2, .03, .25)
        n = int(4 * BEAT * SR)
        tt = np.arange(n) / SR
        add(np.sin(2 * np.pi * hz(BAS[k % 4]) * tt) * env(n, .05, .3), t, 0, .09)
        t += 4 * BEAT
        k += 1
    for s in scenes[1:]:
        whoosh(temps[s["id"]]["debut"] / 1000)
    al = temps["alerte"]["debut"] / 1000
    for i, m in enumerate((84, 88)):   # « ding » de l'e-mail qui arrive
        bell(m, al + .9 + i * .14, .07, .3)
    for nom, note in (("essentiel", 79), ("pro", 83), ("cle", 86)):
        bell(note, temps[nom]["debut"] / 1000 + .25, .05, -.2)
    for i, ms in enumerate(temps["choisir"]["marks"][1:]):
        bell((79, 83, 86)[i], temps["choisir"]["debut"] / 1000 + ms / 1000, .045, (-.4, 0, .4)[i])
    for i, m in enumerate((72, 76, 79, 84, 88)):   # victoire
        bell(m, gagne + .3 + i * .09, .06, -.4 + .2 * i)
    pad([48, 55, 60, 64, 67, 72], fin, total - fin + .8, .03)
    for i, m in enumerate((72, 79, 84)):
        bell(m, fin + .2 + i * .15, .05, -.3 + .3 * i)

    # Réverbération légère (réponse impulsionnelle synthétique, convolution FFT)
    ir_n = int(1.6 * SR)
    ir = rng.standard_normal(ir_n) * np.exp(-np.arange(ir_n) / SR * 4.2)
    ir = lowpass_fft(ir, 5000)
    ir /= np.abs(ir).sum() / 6

    def conv(x):
        m = len(x) + ir_n
        return np.fft.irfft(np.fft.rfft(x, m) * np.fft.rfft(ir, m), m)[:len(x)]
    L, R = L + .18 * conv(L), R + .18 * conv(R)
    bed_peak = max(np.abs(L).max(), np.abs(R).max(), 1e-9)
    L, R = L / bed_peak * .3, R / bed_peak * .3

    # 3. Mixage : le fond baisse quand la voix parle (ducking), voix légèrement réverbérée
    e = np.convolve(np.abs(V), np.ones(int(.12 * SR)) / int(.12 * SR), "same")
    e = np.convolve((e > .01).astype(float), np.ones(int(.4 * SR)) / int(.4 * SR), "same")
    duck = 1 - .55 * np.clip(e, 0, 1)
    vr = V + .07 * conv(V)
    fade = np.clip((total + .6 - np.arange(N) / SR) / 2.2, 0, 1) * np.minimum(1, np.arange(N) / SR / .4)
    out = np.stack([(vr + L * duck) * fade, (vr + R * duck) * fade], 1)[:int(SR * (total + .6))]
    out *= .92 / np.abs(out).max()
    mp3 = RACINE / ("out/film-sans-voix.mp3" if sans_voix else "static/film.mp3")
    mp3.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        with wave.open(f.name, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((out * 32767).astype("<i2").tobytes())
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", f.name, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                        "-ar", str(SR), "-codec:a", "libmp3lame", "-b:a", "128k", str(mp3)], check=True)
    if not sans_voix:
        (RACINE / "config" / "film_temps.json").write_text(json.dumps({"total_ms": round(total * 1000), "scenes": temps}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(scenes)} scènes, {len(pistes)} phrases, durée {total:.1f} s : " + ", ".join(f"{k} {v['ms'] / 1000:.1f}" for k, v in temps.items()))


main()
