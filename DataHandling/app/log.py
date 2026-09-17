"""
Coloured terminal logging for the HealthFlex customer-agent server.

All output goes to stdout so Docker / EC2 `docker logs` picks it up.
ANSI codes render in any modern terminal and in AWS CloudWatch log tailing.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

# ── ANSI codes ─────────────────────────────────────────────────────────────
R   = "\033[0m"       # reset
B   = "\033[1m"       # bold
DIM = "\033[2m"

BLK = "\033[30m";  RED     = "\033[31m";  GRN = "\033[32m";  YLW = "\033[33m"
BLU = "\033[34m";  MAG     = "\033[35m";  CYN = "\033[36m";  WHT = "\033[37m"
BRED = "\033[91m"; BGRN    = "\033[92m";  BYLW = "\033[93m"; BBLU = "\033[94m"
BMAG = "\033[95m"; BCYN    = "\033[96m";  BWHT = "\033[97m"

BGGRN = "\033[42m"; BGRED = "\033[41m"; BGBLU = "\033[44m"; BGYLW = "\033[43m"


# ── Helpers ─────────────────────────────────────────────────────────────────
def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

def _p(*args, **kw):
    print(*args, **kw, flush=True)


# ── Startup ─────────────────────────────────────────────────────────────────
def banner():
    _p(f"""
{B}{BCYN}╔══════════════════════════════════════════════════╗
║   🏥  HealthFlex  Customer Agent  ·  Starting…   ║
╚══════════════════════════════════════════════════╝{R}""")


def progress_bar(label: str, total: int = 20, width: int = 28, delay: float = 0.025):
    """Animated progress bar — call once, blocks for ~total*delay seconds."""
    for i in range(total + 1):
        filled = int(width * i / total)
        bar = f"{BGRN}{'█' * filled}{R}{DIM}{'░' * (width - filled)}{R}"
        pct = int(100 * i / total)
        _p(f"\r  {CYN}{label:<30}{R}  [{bar}]  {B}{pct:>3}%{R}", end="", file=sys.stdout)
        if i < total:
            time.sleep(delay)
    _p()


def step_ok(label: str, detail: str = ""):
    suffix = f"  {DIM}{detail}{R}" if detail else ""
    _p(f"  {BGRN}▶{R}  {label}{suffix}")

def step_warn(label: str, detail: str = ""):
    suffix = f"  {DIM}{detail}{R}" if detail else ""
    _p(f"  {BYLW}⚠{R}  {YLW}{label}{R}{suffix}")

def step_fail(label: str, detail: str = ""):
    suffix = f"  {DIM}{detail}{R}" if detail else ""
    _p(f"  {BRED}✗{R}  {RED}{label}{R}{suffix}")

def divider(char: str = "─", width: int = 54):
    _p(f"{DIM}{char * width}{R}")


# ── WebSocket lifecycle ──────────────────────────────────────────────────────
def ws_connect(client_id: str, origin: str = ""):
    env_tag = f"  {BCYN}[DEV]{R}" if "dev." in origin else f"  {BGRN}[PROD]{R}"
    _p(f"{BGRN}{B}++  CONNECT   {R}{B}{client_id}{R}{env_tag}  {DIM}{_ts()}{R}")

def ws_disconnect(client_id: str, reason: str = ""):
    note = f"  {DIM}{reason}{R}" if reason else ""
    _p(f"{BRED}{B}--  DISCONNECT{R}  {DIM}{client_id}{R}{note}  {DIM}{_ts()}{R}")

def ws_error(client_id: str, error: str):
    _p(f"{RED}{B}✗✗  WS ERROR  {R}  {DIM}{client_id}{R}  {RED}{error}{R}  {DIM}{_ts()}{R}")


# ── Interview events ─────────────────────────────────────────────────────────
def interview_start(user_id: str, form_id: str, mode: str = "NEW"):
    """mode: NEW | RESUME | PROM"""
    icons = {"NEW": ("▶", BGRN), "RESUME": ("↺", BCYN), "PROM": ("📋", BMAG)}
    icon, color = icons.get(mode, ("▶", BGRN))
    _p(f"{color}{B}{icon}  INTERVIEW {mode:<6}{R}  user={B}{user_id}{R}  form={CYN}{form_id}{R}  {DIM}{_ts()}{R}")

def interview_question(question_preview: str, section: str = "", idx: int = 0):
    preview = question_preview[:70] + "…" if len(question_preview) > 70 else question_preview
    sec = f"  {DIM}§{section}[{idx}]{R}" if section else ""
    _p(f"  {BBLU}Q{R}  {preview!r}{sec}")

def user_message(user_id: str, text: str):
    preview = text[:65] + "…" if len(text) > 65 else text
    _p(f"  {MAG}▸ MSG{R}  {DIM}{user_id}{R}  {preview!r}")

def audio_received(stt_ms: float, transcript: str):
    preview = transcript[:65] + "…" if len(transcript) > 65 else transcript
    _p(f"  {CYN}🎙  AUDIO{R}  {DIM}STT={stt_ms:.0f}ms{R}  {preview!r}")


# ── MongoDB / persistence ────────────────────────────────────────────────────
def db_save(user_id: str, form_id: str, db_name: str, is_dev: bool = False):
    db_color = BCYN if is_dev else BGRN
    env = f"  {BCYN}DEV{R}" if is_dev else f"  {BGRN}PROD{R}"
    _p(f"  {GRN}💾  SAVE{R}  {B}{user_id[:24]}{R}  form={CYN}{form_id}{R}"
       f"  → {db_color}{db_name}{R}{env}  {DIM}{_ts()}{R}")

def db_connected(uri_label: str, db_name: str, collections: list[str]):
    cols = ", ".join(collections)
    _p(f"  {BGRN}🗄  DB OK{R}  {B}{db_name}{R}  {DIM}({cols}){R}  via {DIM}{uri_label}{R}")

def db_error(label: str, error: str):
    _p(f"  {BRED}🗄  DB ERR{R}  {RED}{label}{R}  {DIM}{error}{R}")


# ── Generic log levels ───────────────────────────────────────────────────────
def info(tag: str, msg: str):
    _p(f"  {BLU}[{tag}]{R}  {msg}")

def ok(tag: str, msg: str):
    _p(f"  {BGRN}✓ [{tag}]{R}  {msg}")

def warn(tag: str, msg: str):
    _p(f"  {BYLW}⚠ [{tag}]{R}  {YLW}{msg}{R}")

def error(tag: str, msg: str):
    _p(f"  {BRED}✗ [{tag}]{R}  {RED}{msg}{R}")

def timing(tag: str, ms: float):
    color = BGRN if ms < 500 else (BYLW if ms < 2000 else BRED)
    _p(f"  {DIM}⏱ [{tag}]{R}  {color}{ms:.0f}ms{R}")
