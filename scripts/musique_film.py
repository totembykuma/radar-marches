"""Compose la musique du film de l'accueil (création originale, libre de droits) : static/film.mp3.
Synthèse pure en numpy, 120 BPM, calée sur les scènes du film. Usage : python3 scripts/musique_film.py"""
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
BPM = 120
BEAT = 60 / BPM
DUREE = 60.0
VICTOIRE = 39.0   # début de la scène « marché gagné »
FIN = 51.5        # début de l'écran final
OUT = Path(__file__).resolve().parent.parent / "static" / "film.mp3"

N = int(SR * DUREE)
L = np.zeros(N)
R = np.zeros(N)
rng = np.random.default_rng(7)


def hz(n):
    return 440 * 2 ** ((n - 69) / 12)


def env(n, a=.01, d=.2, s=.6, r=.3, hold=None):
    t = np.arange(n) / SR
    hold = hold if hold is not None else n / SR - r
    e = np.where(t < a, t / a, np.where(t < a + d, 1 - (1 - s) * (t - a) / d, s))
    rel = np.clip((t - hold) / r, 0, 1)
    return e * (1 - rel)


def add(sig, t0, pan=0.0, gain=1.0):
    i = int(t0 * SR)
    if i >= N:
        return
    sig = sig[: N - i] * gain
    L[i:i + len(sig)] += sig * (1 - pan) ** .5 * .7071 * 1.414 / 2 * 2 ** .5
    R[i:i + len(sig)] += sig * (1 + pan) ** .5 * .7071 * 1.414 / 2 * 2 ** .5


def lowpass(x, a):
    y = np.empty_like(x)
    acc = 0.0
    for k in range(len(x)):
        acc += a * (x[k] - acc)
        y[k] = acc
    return y


def pad(notes, t0, dur, gain=.05):
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = np.zeros(n)
    for m in notes:
        for det in (-.12, .0, .12):
            f = hz(m + det)
            s += np.sin(2 * np.pi * f * t) + .25 * np.sin(4 * np.pi * f * t + .3)
    s *= env(n, a=.6, d=.4, s=.85, r=.8)
    add(s, t0, pan=-.25, gain=gain)
    add(s, t0 + .011, pan=.25, gain=gain)


def pluck(m, t0, dur=.45, gain=.16, pan=0.0):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = hz(m)
    s = (np.sin(2 * np.pi * f * t) + .4 * np.sin(4 * np.pi * f * t) + .15 * np.sin(6 * np.pi * f * t)) * np.exp(-t * 7)
    s *= env(n, a=.003, d=.05, s=1, r=.05)
    add(s, t0, pan, gain)


def bell(m, t0, gain=.12, pan=0.0):
    n = int(2.2 * SR)
    t = np.arange(n) / SR
    f = hz(m)
    s = (np.sin(2 * np.pi * f * t) + .5 * np.sin(2 * np.pi * f * 2.76 * t) * np.exp(-t * 3) + .25 * np.sin(2 * np.pi * f * 5.4 * t) * np.exp(-t * 6)) * np.exp(-t * 1.6)
    add(s * env(n, a=.002, d=.1, s=1, r=.2), t0, pan, gain)


def bass(m, t0, dur, gain=.2):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = hz(m)
    s = np.sin(2 * np.pi * f * t) + .3 * np.sin(4 * np.pi * f * t)
    add(s * env(n, a=.01, d=.15, s=.7, r=.08), t0, 0, gain)


def kick(t0, gain=.5):
    n = int(.35 * SR)
    t = np.arange(n) / SR
    ph = 2 * np.pi * np.cumsum(45 + 110 * np.exp(-t * 30)) / SR
    add(np.sin(ph) * np.exp(-t * 9), t0, 0, gain)


NOISE = rng.standard_normal(int(SR * 2))


def hat(t0, gain=.05, pan=.2):
    n = int(.06 * SR)
    t = np.arange(n) / SR
    s = np.diff(NOISE[:n + 1]) * np.exp(-t * 70)
    add(s, t0, pan, gain)


def clap(t0, gain=.08):
    n = int(.2 * SR)
    t = np.arange(n) / SR
    s = lowpass(NOISE[:n], .35) * (np.exp(-t * 25) + .5 * np.exp(-np.maximum(t - .012, 0) * 30) * (t > .012))
    add(s, t0, -.1, gain)


def riser(t0, dur, gain=.07):
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = rng.standard_normal(n)
    out = np.empty(n)
    acc = 0.0
    for k in range(n):
        acc += (.02 + .5 * (k / n) ** 2) * (s[k] - acc)
        out[k] = acc
    add(out * (t / dur) ** 2, t0, 0, gain)


# Grille d'accords (une mesure de 4 temps chacun) : Do maj7, La m7, Fa maj7, Sol
ACC = [(60, 64, 67, 71), (57, 60, 64, 67), (53, 57, 60, 64), (55, 59, 62, 67)]
BASSES = [36, 33, 29, 31]
MESURE = 4 * BEAT

t, k = 0.0, 0
while t < FIN:
    a, b = ACC[k % 4], BASSES[k % 4]
    gagne = t >= VICTOIRE - .01
    pad([x + (12 if gagne else 0) for x in a[:3]] + [a[3]], t, MESURE + .6, gain=.035 if t < 6 else .045)
    # arpège : discret au début, plus présent ensuite
    motif = [0, 1, 2, 3, 2, 1, 2, 3] if not gagne else [0, 2, 3, 2, 1, 3, 2, 3]
    for s, idx in enumerate(motif):
        ts = t + s * BEAT / 2
        if ts >= FIN:
            break
        pluck(a[idx] + 12, ts, gain=(.08 if t < 6 else .13), pan=(-.35 if s % 2 else .35))
    if t >= 12 - .01:
        for s in range(4):
            ts = t + s * BEAT
            if ts >= FIN or (VICTOIRE - 2 <= ts < VICTOIRE):
                continue
            kick(ts, gain=.42)
            hat(ts + BEAT / 2, gain=.045)
            if s in (1, 3) and t >= 25:
                clap(ts, gain=.07)
        for s, dur in ((0, 1.5), (1.5, .5), (2, 1.5), (3.5, .5)):
            ts = t + s * BEAT
            if ts < FIN and not (VICTOIRE - 2 <= ts < VICTOIRE):
                bass(b + (12 if s == 1.5 else 0), ts, dur * BEAT, gain=.2)
    if gagne:
        mel = [76, 79, 81, 79, 76, 74, 76, 72]
        for s, m in enumerate(mel):
            ts = t + s * BEAT / 2
            if ts < FIN:
                bell(m - (0 if k % 2 == 0 else 2) + (0 if s < 4 else 0), ts, gain=.055, pan=.15)
    t += MESURE
    k += 1

# Petits accents synchronisés avec le film
for ts, m in ((3.85, 84), (9.6, 88), (15.5, 83), (44.0, 86)):
    bell(m, ts, gain=.06, pan=.3)
riser(VICTOIRE - 2, 2, gain=.08)
for m in (72, 76, 79, 84):
    bell(m, VICTOIRE, gain=.09)
kick(VICTOIRE, gain=.6)
# Final : accord de Do qui résonne
pad([48, 55, 60, 64, 67, 72], FIN, 7.5, gain=.05)
for i, m in enumerate((72, 76, 79, 84)):
    bell(m, FIN + i * .12, gain=.07, pan=(-.3 + .2 * i))
bass(36, FIN, 4, gain=.22)
kick(FIN, gain=.5)

# Réverbération légère (peignes en parallèle) puis normalisation et fondu de sortie
def reverb(x):
    y = np.zeros_like(x)
    for d, g in ((1557, .78), (1617, .76), (1491, .77), (1422, .75)):
        buf = x.copy()
        for k in range(d, len(buf), d):
            buf[k:k + d] += g * buf[k - d:k][: len(buf[k:k + d])]
        y += buf
    return y / 4


wet_l, wet_r = reverb(L), reverb(R)
L2, R2 = L + .22 * wet_l, R + .22 * wet_r
fade = np.clip((DUREE - np.arange(N) / SR) / 3.0, 0, 1)
L2, R2 = L2 * fade, R2 * fade
peak = max(np.abs(L2).max(), np.abs(R2).max())
st = (np.stack([L2, R2], 1) / peak * .89 * 32767).astype("<i2")
wav = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/film.wav")
with wave.open(str(wav), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(st.tobytes())
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-af", "loudnorm=I=-16:TP=-1.5", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "112k", str(OUT)], check=True)
print("ok", OUT, OUT.stat().st_size // 1024, "Ko")
