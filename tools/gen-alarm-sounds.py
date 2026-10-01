#!/usr/bin/env python3
"""Clock's alarm sounds, synthesized (no samples: Sonata's own, CC0):
    python3 tools/gen-alarm-sounds.py   # writes sonata2/data/sounds/sonata-alarm-*.oga (needs numpy, ffmpeg)"""
import os
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sonata2", "data", "sounds"))
import numpy as np, wave, subprocess
SR = 48000

def env(n, attack=0.005, decay=1.2):
    t = np.arange(n) / SR
    a = np.clip(t / attack, 0, 1)
    return a * np.exp(-t / decay)

def note(f, dur, partials=((1, 1.0),), decay=1.0, attack=0.004, detune=0.0):
    n = int(dur * SR); t = np.arange(n) / SR
    s = np.zeros(n)
    for k, amp in partials:
        s += amp * np.sin(2 * np.pi * f * k * (1 + detune) * t) * np.exp(-t * k * 0.6 / decay)
    return s * env(n, attack, decay)

def mix(events, total):
    out = np.zeros(int(total * SR))
    for start, sig in events:
        i = int(start * SR); out[i:i + len(sig)] += sig[:len(out) - i]
    return out

def save(name, sig):
    sig = sig / max(1e-9, np.abs(sig).max()) * 0.8
    fade = int(0.05 * SR); sig[-fade:] *= np.linspace(1, 0, fade)
    pcm = (sig * 32767).astype(np.int16)
    with wave.open(name + ".wav", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", name + ".wav", "-c:a", "libvorbis", "-q:a", "4",
                    name + ".oga"], check=True)
    os.remove(name + ".wav")

C5, E5, G5, A5, C6, D5, B4, G4, E4 = 523.25, 659.25, 783.99, 880.0, 1046.5, 587.33, 493.88, 392.0, 329.63
marimba = ((1, 1.0), (4, 0.25), (10, 0.05))
# Morning: soft marimba arpeggio, up and settling (the default)
save("sonata-alarm-morning", mix([(0.0, note(C5, 1.2, marimba, 0.45)), (0.18, note(E5, 1.2, marimba, 0.45)),
                                  (0.36, note(G5, 1.2, marimba, 0.45)), (0.54, note(C6, 1.6, marimba, 0.55)),
                                  (0.95, note(G5, 1.4, marimba, 0.5))], 2.6))
# Chimes: bell partials, slow
bell = ((1, 1.0), (2.76, 0.35), (5.4, 0.12), (8.93, 0.05))
save("sonata-alarm-chimes", mix([(0.0, note(G4, 2.5, bell, 1.1)), (0.5, note(D5, 2.5, bell, 1.1)),
                                 (1.0, note(B4, 2.5, bell, 1.1))], 3.4))
# Harp: plucked strings (Karplus-Strong), rising
def pluck(f, dur):
    n = int(dur * SR); p = int(SR / f)
    buf = np.random.default_rng(int(f)).uniform(-1, 1, p)
    for _ in range(6):                                   # a soft pluck: smoothed excitation
        buf = 0.5 * (buf + np.roll(buf, 1))
    out = np.zeros(n)
    for i in range(n):
        out[i] = buf[i % p]
        buf[i % p] = 0.996 * 0.5 * (buf[i % p] + buf[(i + 1) % p])
    out = out * env(n, 0.002, 1.5)
    k = np.ones(12) / 12                                  # and a gentle low-pass
    return np.convolve(out, k, mode="same")
save("sonata-alarm-harp", mix([(0.0, pluck(G4, 1.5)), (0.14, pluck(C5, 1.5)), (0.28, pluck(E5, 1.5)),
                               (0.42, pluck(G5, 1.5)), (0.56, pluck(C6, 1.8))], 2.5))
# Pulse: two soft low beeps (a calm classic)
beep = lambda: note(660, 0.22, ((1, 1.0), (2, 0.1)), 2.0, 0.01) * np.hanning(int(0.22 * SR)) ** 0.3
save("sonata-alarm-pulse", mix([(0.0, beep()), (0.3, beep())], 1.0))
# Sunrise: warm pad swelling in (very gentle)
n = int(3.0 * SR); t = np.arange(n) / SR
pad = sum(a * np.sin(2 * np.pi * f * t) for f, a in ((C5 / 2, 0.6), (E5 / 2, 0.4), (G5 / 2, 0.4), (C5, 0.25)))
pad *= np.sin(np.pi * t / 3.0) ** 2
save("sonata-alarm-sunrise", pad)
