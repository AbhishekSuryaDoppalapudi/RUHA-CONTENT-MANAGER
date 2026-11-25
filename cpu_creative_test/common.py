"""CPU-only creative engine test harness (experimental; does not touch production data). Isolated data dir: ./data"""
import ctypes, json, sys, threading, time
from pathlib import Path
import psutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
OUT = HERE / "out"


class _MS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tot", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                ("ae", ctypes.c_ulonglong)]


def free_gb() -> float:
    s = _MS(); s.l = ctypes.sizeof(_MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return s.avail / 2**30


class Monitor:
    """Samples free system RAM, this process's RSS and CPU %; aborts (flag + unload hook) if free RAM < floor."""
    def __init__(self, floor_gb=0.35, on_abort=None):
        self.floor, self.on_abort = floor_gb, on_abort
        self.min_free = free_gb(); self.peak_rss = 0.0; self.cpu = []; self.aborted = False
        self._stop = threading.Event(); self._p = psutil.Process()
        self.start_free = self.min_free

    def _run(self):
        psutil.cpu_percent(None)
        while not self._stop.wait(0.5):
            f = free_gb(); self.min_free = min(self.min_free, f)
            self.peak_rss = max(self.peak_rss, self._p.memory_info().rss / 2**30)
            self.cpu.append(psutil.cpu_percent(None))
            if f < self.floor and not self.aborted:
                self.aborted = True
                if self.on_abort: self.on_abort()

    def __enter__(self):
        self.t0 = time.time(); self._th = threading.Thread(target=self._run, daemon=True); self._th.start(); return self

    def __exit__(self, *a):
        self._stop.set(); self._th.join(); self.secs = time.time() - self.t0

    def summary(self):
        return {"seconds": round(self.secs, 1), "free_gb_start": round(self.start_free, 2), "free_gb_min": round(self.min_free, 2),
                "python_peak_rss_gb": round(self.peak_rss, 2), "cpu_avg_pct": round(sum(self.cpu) / max(1, len(self.cpu)), 0),
                "cpu_max_pct": max(self.cpu or [0]), "aborted_for_low_ram": self.aborted}


def build_app(real_llm=True):
    from ruha.ai.embeddings import HashEmbedder
    from ruha.config import Settings
    from ruha.context import Services
    from ruha.main import create_app
    s = Settings(data_dir=HERE / "data", start_worker=False)
    s.ensure_dirs()
    app = create_app(s, Services(embedder_factory=lambda cfg: HashEmbedder()))
    return app


SERMON = [
 "Good morning church. Today I want to talk about something most of us spend our whole lives hiding, and that is our weakness.",
 "We learn very early to look strong. We smile at the door, we say we are fine, and we carry our struggles home in a closed bag.",
 "But I have good news. God does not require you to pretend to be strong. He never asked you to put on a performance for Him.",
 "Look at the apostle Paul. He asked God three times to take away the thorn in his flesh, and God did not remove it.",
 "Instead God said to him in Second Corinthians twelve verse nine, my grace is sufficient for you, for my power is made perfect in weakness.",
 "Think about that. God's power is not made perfect in your performance. It is made perfect in your weakness.",
 "So the first step is to recognize your weakness. Name it. Stop calling it something else. Say it honestly before God.",
 "A cracked clay pot is not useless to God. The light shines through the cracks, and the cracks become the very place where His glory is seen.",
 "The second step is surrender. Surrender is not giving up. Surrender is placing the thing you cannot carry into hands that can.",
 "Many of us pray for God to make us stronger, but we never open our hands. You cannot receive with a clenched fist.",
 "The third step is prayer. Not polished prayer, not long prayer. Just an honest, trembling, tell me the truth kind of prayer.",
 "When your strength ends, His strength begins. That is not a slogan. That is the testimony of every saint who ever walked through the valley.",
 "I remember a season when I could not get out of bed. I was a pastor, and I was too ashamed to tell anyone I was falling apart.",
 "When I finally knelt on the floor and said God I have nothing left, something shifted. I did not feel strong. But I felt held.",
 "That is transformation. Transformation does not begin when you are strong enough. It begins when you are honest enough.",
 "And here is the hope. Your weakness is not the end of your story. It is the doorway to His strength.",
 "So today, do not hide it. Bring your weakness to the altar. Surrender it, pray over it, and watch God grow your faith through His strength.",
 "Let us pray. Father, we are tired of pretending. We bring You our weakness, and we receive Your strength. In Jesus name, amen.",
]


def segments():
    out, t = [], 0.0
    for s in SERMON:
        d = round(len(s.split()) / 2.6, 1)           # ~156 words per minute
        out.append({"start": round(t, 1), "end": round(t + d, 1), "text": s}); t += d + 0.4
    return out
