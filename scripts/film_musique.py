"""Musique du film de l'accueil : petit morceau de jazz d'ambiance, composé et synthétisé ici (création originale, aucun droit à payer).
Trio piano électrique + contrebasse + balais, swing à 88 battements/min, grille Fmaj9 | Dm9 | Gm9 | C13 | Am7 D7 | Gm9 C13 | Fmaj9.
Écrit static/film.mp3 (≈ 19 s, fin en douceur). Usage : python scripts/film_musique.py   (numpy + ffmpeg)"""
import subprocess
from pathlib import Path

import numpy as np

RACINE = Path(__file__).resolve().parent.parent
SR = 44100
BPM = 88
B = 60 / BPM                      # durée d'un temps (s)
SW = 2 / 3                        # croche swing : la 2e croche tombe aux 2/3 du temps
MESURES = 7
DUREE = (MESURES - 1) * 4 * B + 3.0   # dernier accord qui résonne 3 s
rng = np.random.default_rng(7)

# (temps de début dans la mesure, accord) ; notes MIDI : basse fondamentale, voicing du piano (sans fondamentale)
F, D, G, C, A = 41, 38, 43, 36, 45
GRILLE = [
    [(0, F, [57, 60, 64, 67])],                              # Fmaj9 : A C E G
    [(0, D, [53, 57, 60, 64])],                              # Dm9   : F A C E
    [(0, G, [58, 62, 65, 69])],                              # Gm9   : Bb D F A
    [(0, C, [58, 62, 64, 69])],                              # C13   : Bb D E A
    [(0, A, [55, 60, 64, 67]), (2, D, [54, 60, 63, 69])],    # Am7 G C E G, D7(b9) F# C Eb A
    [(0, G, [58, 62, 65, 69]), (2, C, [58, 62, 64, 69])],    # Gm9, C13
    [(0, F, [57, 60, 64, 67, 72])],                          # Fmaj9 final
]
# mélodie au vibraphone : (mesure, temps, durée en temps, note)
MELODIE = [
    (0, SW, .5, 69), (0, 1, 1.5, 72), (0, 2 + SW, 1.4, 76),
    (1, 1, .66, 74), (1, 1 + SW, .4, 72), (1, 2, 1, 69), (1, 3, 1, 65),
    (2, SW, .4, 70), (2, 1, 1, 74), (2, 2, 1.6, 77), (2, 3 + SW, .4, 76),
    (3, 0, 1.8, 74), (3, 2, .66, 70), (3, 2 + SW, .4, 69), (3, 3, 1, 67),
    (4, SW, .4, 72), (4, 1, 1, 76), (4, 2, .66, 75), (4, 2 + SW, .4, 72), (4, 3, 1, 69),
    (5, 0, 1.6, 70), (5, 1 + SW, .4, 69), (5, 2, 1, 67), (5, 3, .66, 64), (5, 3 + SW, .4, 67),
    (6, 0, 4, 69),
]


def hz(n):
    return 440 * 2 ** ((n - 69) / 12)


def t_(mesure, temps):
    return (mesure * 4 + temps) * B


def lowpass(x, fc):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.fft.irfft(X / (1 + (f / fc) ** 4), len(x))


def bandpass(x, lo, hi):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.fft.irfft(X / (1 + (lo / np.maximum(f, 1)) ** 4) / (1 + (f / hi) ** 4), len(x))


L = np.zeros(int(SR * DUREE) + SR)
R = np.zeros_like(L)


def add(sig, t0, g=1.0, pan=0.0):
    i = int(t0 * SR)
    n = min(len(sig), len(L) - i)
    L[i:i + n] += sig[:n] * g * np.sqrt((1 - pan) / 2)
    R[i:i + n] += sig[:n] * g * np.sqrt((1 + pan) / 2)


def rhodes(note, dur, vel=1.0):
    """Piano électrique (synthèse FM : barre + marteau), tenue puis relâché court."""
    n = int(SR * (dur + .35))
    t = np.arange(n) / SR
    f = hz(note)
    idx = 1.3 * vel * np.exp(-t / .25) + .15
    s = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t))
    s += .12 * np.sin(2 * np.pi * f * 7.03 * t) * np.exp(-t / .03)          # « tine » à l'attaque
    a = np.minimum(1, t / .004) * np.exp(-t / 1.6) * np.clip((dur + .35 - t) / .35, 0, 1)
    return s * a * (1 + .12 * np.sin(2 * np.pi * 4.6 * t)) * vel


def vibra(note, dur):
    n = int(SR * (dur + 1.2))
    t = np.arange(n) / SR
    f = hz(note)
    s = np.sin(2 * np.pi * f * t) + .25 * np.sin(2 * np.pi * f * 4 * t) * np.exp(-t / .35) + .08 * np.sin(2 * np.pi * f * 10 * t) * np.exp(-t / .06)
    a = np.minimum(1, t / .003) * np.exp(-t / 1.9) * np.clip((dur + 1.2 - t) / 1.2, 0, 1)
    return s * a * (1 + .22 * np.sin(2 * np.pi * 5.2 * t))


def basse(note, dur):
    """Contrebasse pincée : harmoniques qui s'éteignent vite, un peu de bois à l'attaque."""
    n = int(SR * (dur + .12))
    t = np.arange(n) / SR
    f = hz(note)
    s = sum(np.sin(2 * np.pi * f * k * t) * np.exp(-t * (1.5 + 3.2 * k)) / k ** .9 for k in range(1, 7))
    s += .3 * lowpass(rng.standard_normal(n), 900) * np.exp(-t / .012)
    a = np.minimum(1, t / .006) * np.exp(-t / 1.4) * np.clip((dur + .12 - t) / .12, 0, 1)
    return lowpass(s * a, 1400)


def ride(g=1.0):
    n = int(SR * 1.4)
    t = np.arange(n) / SR
    s = sum(np.sin(2 * np.pi * fr * t + rng.uniform(0, 6)) for fr in (3150, 4420, 5310, 6870, 8030, 9410)) / 6
    s = .55 * s + .45 * bandpass(rng.standard_normal(n), 5000, 12000)
    return s * (np.exp(-t / .5) * .8 + np.exp(-t / .03) * .6) * g


def charley():
    n = int(SR * .09)
    t = np.arange(n) / SR
    return bandpass(rng.standard_normal(n), 6000, 11000) * np.exp(-t / .022)


def balai():
    """Balai sur la caisse claire : souffle doux qui monte puis retombe."""
    n = int(SR * .32)
    t = np.arange(n) / SR
    return bandpass(rng.standard_normal(n), 1800, 6500) * np.minimum(1, t / .05) * np.exp(-t / .12)


def marche(racine, suivante, temps):
    """Ligne de contrebasse « walking » : fondamentale, tierce, quinte, note d'approche de l'accord suivant."""
    tierce = racine + (3 if racine in (D, G, A) else 4)
    notes = [racine, tierce, racine + 7, suivante + (1 if (temps // 2) % 2 else -1)]
    return notes[:temps]


# 1. Piano : attaque sur le 1, puis sur la croche swing du 2 (rythme « Charleston »), voicings tenus
for m, mesure in enumerate(GRILLE):
    for k, (deb, rac, voic) in enumerate(mesure):
        fin = mesure[k + 1][0] if k + 1 < len(mesure) else 4
        if m == len(GRILLE) - 1:   # accord final : arpège doux puis tenue
            for j, n in enumerate(voic):
                add(rhodes(n, 3.6, .8), t_(m, 0) + j * .045, .085, -.25 + .1 * j)
            continue
        coups = [(deb, .55, .9), (deb + 1 + SW, fin - deb - 1 - SW, .7)] if fin - deb == 4 else [(deb, .4, .85), (deb + 1 + SW, .3, .65)]
        for c, longueur, vel in coups:
            for j, n in enumerate(voic):
                add(rhodes(n, longueur * B, vel), t_(m, c) + j * .008, .085, -.3 + .15 * j)

# 2. Contrebasse : 4 noires par mesure, une seule note tenue à la fin
racines = [(m, deb, rac, (mes[k + 1][0] if k + 1 < len(mes) else 4) - deb) for m, mes in enumerate(GRILLE) for k, (deb, rac, _) in enumerate(mes)]
for i, (m, deb, rac, nb) in enumerate(racines):
    if m == len(GRILLE) - 1:
        add(basse(rac - 12 if rac > 40 else rac, 3.2), t_(m, 0), .2)
        continue
    suiv = racines[i + 1][2]
    for j, n in enumerate(marche(rac, suiv, nb)):
        n = n - 12 if n > 47 else n
        add(basse(n, B * .92), t_(m, deb + j), .2 * (1 if j == 0 else .85))

# 3. Batterie aux balais : ride « ding, ding-da », charleston au pied sur 2 et 4, balai sur 2 et 4
for m in range(MESURES - 1):
    for b in range(4):
        add(ride(.75 if b % 2 else .6), t_(m, b), .16, .45)
        if b % 2:
            add(ride(.4), t_(m, b + SW), .16, .45)
            add(charley(), t_(m, b), .14, .2)
            add(balai(), t_(m, b) - .03, .15, -.15)
add(ride(.9), t_(MESURES - 1, 0), .18, .45)

# 4. Vibraphone : la mélodie
for m, b, d, n in MELODIE:
    add(vibra(n, d * B), t_(m, b), .11, .15)

# 5. Réverbération (petite salle), fondu final, mise au niveau
ir_n = int(SR * 1.6)
ir_t = np.arange(ir_n) / SR
sortie = []
for x in (L, R):
    ir = lowpass(rng.standard_normal(ir_n), 5000) * np.exp(-ir_t / .45)
    ir /= np.sqrt((ir ** 2).sum())
    wet = np.fft.irfft(np.fft.rfft(x, len(x) + ir_n) * np.fft.rfft(ir, len(x) + ir_n), len(x) + ir_n)[:len(x)]
    sortie.append(x + .35 * wet)
st = np.stack(sortie, 1)[:int(SR * DUREE)]
t = np.arange(len(st)) / SR
st *= np.clip((DUREE - t) / 1.2, 0, 1)[:, None] * np.minimum(1, t / .02)[:, None]
st = np.tanh(1.2 * st / np.abs(st).max()) / np.tanh(1.2) * .89

sortie = RACINE / "static" / "film.mp3"
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                "-af", "loudnorm=I=-19:TP=-1.5:LRA=11", "-ar", str(SR), "-c:a", "libmp3lame", "-b:a", "160k", str(sortie)],
               input=st.astype("<f4").tobytes(), check=True)
print(f"{sortie} : {DUREE:.1f} s, {sortie.stat().st_size // 1024} Ko")
