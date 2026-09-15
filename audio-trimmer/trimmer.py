#!/usr/bin/env python3
"""
trimmer.py — top and tail a recording by ear, add your music, export the MP3.

One file at a time. Drop a recording in, play it until the real opening line,
press Set start here, do the same at the end, press Build. You get an MP3 with
your intro in front and your outro behind.

WHAT IT DOES, IN ORDER
  1. Trim    cut everything before your start point and after your end point
  2. Cut     take out the patches you marked in the middle — retakes, false
             starts, the phone going off — and rejoin what's left
  3. Level   normalise the speech to -16 LUFS (optional, on by default)
  4. Join    intro, then the episode, then outro
  5. Encode  MP3 at 128kbps

FINDING THE RETAKES
Drop the session transcript in and it lists every moment somebody asked to go
again, with the words around it. It shows rather than decides: one real
transcript says "if you wanna stop and say, hey, let's redo this, that's fine
too", which matches every pattern going and is a conversation about retakes
rather than one. A filter clever enough to drop that would sometimes drop the
real one instead.

Every join gets a 30ms fade. Butt-joining two pieces of audio puts a step in
the waveform, and a step is an audible click — the one artefact that makes an
otherwise clean edit sound amateur.

YOUR MUSIC
Uploaded through the page and kept in music/ next to this file, so it's set
once and used on every recording afterwards. Replacing the intro clears
whatever was in that slot before, whatever extension it had — otherwise an
old intro.wav sits beside a new intro.mp3 and neither of you knows which one
is being used.

FFMPEG
This needs ffmpeg, which does the actual audio work. There is no realistic
way to do this properly without it. If it isn't installed the page says so
and prints the one line to paste.

Nothing leaves this machine. Nothing is uploaded anywhere.
"""

import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
# Finished files go in their own folder, never beside the recording. When they
# shared one, the MP3 written by a build looked like a recording to the next
# build, which trimmed it again — "My Episode - trimmed - trimmed - trimmed".
# It compounds silently; the filename is the only clue anything went wrong.
OUT = os.path.join(WORK, "_out")
MUSIC = os.path.join(HERE, "music")
PORT = 8772                    # 8770 and 8771 belong to the other tools

TARGET_LUFS = -16.0            # what Apple, Spotify and the rest expect
TRUE_PEAK = -1.5
LRA = 11.0
BITRATE = "128k"
SR = "48000"
FADE = 0.03                    # 30ms at every join, to kill the click

# Everything ffmpeg can read. Deliberately wide: the answer to "can I use a
# .mov straight off the camera" should be yes.
MEDIA_EXT = (".mp3", ".m4a", ".wav", ".aac", ".flac", ".aiff", ".aif",
             ".ogg", ".oga", ".opus", ".wma", ".amr", ".caf",
             ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi")

STATE = os.path.join(WORK, "state.json")
JOB = {"running": False, "lines": [], "done": False, "ok": False}


# ----------------------------------------------------------------- ffmpeg
def have_ffmpeg():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def ffmpeg_hint():
    """The one command for this machine, not a menu of three."""
    if sys.platform == "darwin":
        return ("brew install ffmpeg",
                "If that says \u201ccommand not found: brew\u201d, install "
                "Homebrew first from brew.sh, then run it again.")
    if os.name == "nt":
        return ("winget install Gyan.FFmpeg",
                "Then close that window and open a new one, so it can find "
                "the new command.")
    return ("sudo apt install ffmpeg", "")


def run_ff(args, label=""):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + args,
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed%s:\n%s"
                           % ((" during " + label) if label else "",
                              p.stderr.strip()[:700]))


def duration(path):
    if not path or not os.path.isfile(path) or not shutil.which("ffprobe"):
        return 0.0
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def hms(sec):
    sec = max(0, int(round(sec or 0)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def parse_ts(v):
    """Accept 90, 1:30, 00:01:30 — whatever someone types under pressure."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    txt = str(v).strip()
    if not txt:
        return None
    try:
        total = 0.0
        for part in txt.split(":"):
            total = total * 60 + float(part or 0)
        return total
    except ValueError:
        return None


# ------------------------------------------------------------------ state
def read_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(st):
    os.makedirs(WORK, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)


def current_file():
    """The recording being worked on, if there is one."""
    if not os.path.isdir(WORK):
        return None
    for f in sorted(os.listdir(WORK)):
        if f.startswith(".") or f.startswith("_"):
            continue
        if not os.path.isfile(os.path.join(WORK, f)):
            continue
        if f.lower().endswith(MEDIA_EXT):
            return f
    return None


def music_path(which):
    if not os.path.isdir(MUSIC):
        return None
    for f in sorted(os.listdir(MUSIC)):
        if f.startswith("."):
            continue
        if os.path.splitext(f)[0].lower() == which:
            return os.path.join(MUSIC, f)
    return None


def output_path():
    st = read_state()
    name = st.get("output")
    p = os.path.join(OUT, name) if name else None
    return p if p and os.path.isfile(p) else None


def check_path():
    p = os.path.join(OUT, "_check.mp3")
    return p if os.path.isfile(p) else None


# ---------------------------------------------------------- retake finder
# What people actually say when a take goes wrong. Taken from real sessions,
# not imagined: "Oh, shoot, I'm sorry... can you repeat that? Let's redo this"
# and "I am one of the founders of, sorry, let me, let me restart."
RETAKE_PHRASES = [
    r"let'?s redo",
    r"(let me|lemme|i'?ll) (just )?(restart|start (over|again)|redo|do that again)",
    r"start (over|again)",
    r"from the top",
    r"(can|could) (you|we) (please )?(repeat|say|ask|do) (that|it|the .{0,24}) again",
    r"(ask|say) (that|the .{0,24}) (one more time|again)",
    r"one more time",
    r"scratch that",
    r"(cut|edit) (that|this) (out|bit)",
    r"take (two|three|2|3)",
    r"my bad",
    r"mess(ed)? (that|it) up",
    r"(let'?s|we should) (do|try) (that|it|this) again",
    r"try (that|it) again",
    r"i'?ll say (that|it) again",
    r"(hang on|hold on),? (let me|can we)",
]
RETAKE_RE = [re.compile(p, re.I) for p in RETAKE_PHRASES]

# Any clock in a line: 1:02, 01:02:03, [00:12:34], with or without decimals.
ANY_TS = re.compile(r"\b(\d{1,2}:\d{2}(?::\d{2})?)(?:[.,]\d{1,3})?\b")


def find_retakes(text):
    """Where in the recording somebody asked to go again.

    Deliberately dumb, and deliberately loud. It does not try to judge whether
    a phrase means a real retake — in one of these recordings the host says
    "if you wanna stop and say, hey, let's redo this, that's fine too", which
    is a discussion about retakes and not one at all. A filter clever enough
    to drop that is also clever enough to drop the real one, and then you are
    scrubbing an hour of audio wondering why it found nothing.

    So it shows every candidate with the sentence around it. Reading twelve
    words tells you instantly which is which; a confident wrong answer does
    not.

    Timestamps are the start of the line the phrase appears in, which is the
    right neighbourhood rather than the exact frame. The retake in the real
    file happens midway through a 72-second turn, so the number gets you
    there and your ears do the last bit.
    """
    hits, last_ts = [], None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        stamps = ANY_TS.findall(line)
        if stamps:
            last_ts = parse_ts(stamps[0])
        if last_ts is None:
            continue
        # The words, with the timestamps and any leading speaker label taken
        # off, so the quote reads like speech.
        words = ANY_TS.sub("", line)
        words = re.sub(r"^\s*(-->|[\d:.,\s]+)", "", words).strip(" \t-–—")
        # A leading "Speaker 1" or "Dana Okafor:" is a label, not speech.
        # Only stripped when a tab, a run of spaces or a colon follows it, so
        # a sentence that opens with a name survives intact.
        words = re.sub(r"^[A-Za-z][\w .'\-]{0,30}?(?:\t+|\s{2,}|:\s)", "",
                       words).strip()
        for rx in RETAKE_RE:
            m = rx.search(words)
            if not m:
                continue
            a = max(0, m.start() - 90)
            quote = words[a:m.end() + 110].strip()
            if a > 0:
                quote = "…" + quote
            if m.end() + 110 < len(words):
                quote += "…"
            hits.append({"at": last_ts, "clock": hms(last_ts),
                         "said": m.group(0), "quote": quote})
            break
    # One per moment: several phrases in one breath is still one retake.
    out = []
    for h in sorted(hits, key=lambda x: x["at"]):
        if out and h["at"] - out[-1]["at"] < 1.0:
            continue
        out.append(h)
    return out


# -------------------------------------------------------------- the cuts
def parse_cuts(raw, floor=0.0, ceil=None):
    """Turn the saved cut rows into clean (from, to) spans in recording time.

    Everything here is in the time shown by the player, which is the time in
    the original recording — not time in the finished file. That distinction
    is the whole game: get it backwards and the cut lands somewhere else
    entirely, and you only find out by listening to the export.

    Bad rows are reported, not silently dropped. A cut you typed and that
    quietly didn't happen is worse than one that refuses.
    """
    good, bad = [], []
    for c in (raw or []):
        a = parse_ts((c or {}).get("from"))
        b = parse_ts((c or {}).get("to"))
        if a is None or b is None:
            if (c or {}).get("from") or (c or {}).get("to"):
                bad.append(c)
            continue
        if b <= a:
            bad.append(c)
            continue
        a, b = max(a, floor), (min(b, ceil) if ceil is not None else b)
        if b - a > 0.05:
            good.append((a, b, str((c or {}).get("why") or "").strip()))
    good.sort(key=lambda x: x[0])
    return good, bad


def merge_spans(spans):
    """Overlapping cuts become one. Two rows covering the same patch would
    otherwise each remove it, and the second removes good audio."""
    out = []
    for a, b, why in spans:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b), out[-1][2])
        else:
            out.append((a, b, why))
    return out


def keep_spans(start, end, cuts, min_piece=0.35):
    """What survives: the trimmed range, minus every cut."""
    keep, at = [], start
    for a, b, _ in cuts:
        if b <= start or a >= end:
            continue
        a, b = max(a, start), min(b, end)
        if a > at:
            keep.append((at, a))
        at = max(at, b)
    if at < end:
        keep.append((at, end))
    # A sliver left between two cuts is a click, not content.
    return [(a, b) for a, b in keep if b - a >= min_piece]


# ------------------------------------------------------------------ build
def say(msg):
    JOB["lines"].append(msg)


def to_wav(src, dst, label):
    """Everything becomes 48k stereo wav before it's joined to anything else.

    Concatenating files that disagree about sample rate or channel count is
    how you get an intro at the wrong speed, or in one ear.
    """
    run_ff(["-i", src, "-ac", "2", "-ar", SR, "-c:a", "pcm_s16le", dst], label)


def fade_piece(src, dst, label):
    """A short fade at both ends of every piece, so the joins don't click."""
    d = duration(src)
    if d <= 2 * FADE:
        shutil.copyfile(src, dst)
        return
    run_ff(["-i", src, "-af",
            "afade=t=in:st=0:d=%s,afade=t=out:st=%.3f:d=%s"
            % (FADE, max(0.0, d - FADE), FADE), dst], label)


def measure_loudness(path):
    """First loudnorm pass: measure, so the second pass can be accurate."""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-af",
         "loudnorm=I=%s:TP=%s:LRA=%s:print_format=json"
         % (TARGET_LUFS, TRUE_PEAK, LRA), "-f", "null", "-"],
        capture_output=True, text=True)
    tail = p.stderr[p.stderr.rfind("{"):p.stderr.rfind("}") + 1]
    try:
        return json.loads(tail)
    except Exception:
        return None


def build():
    JOB.update({"running": True, "lines": [], "done": False, "ok": False})
    tmp = os.path.join(WORK, "_tmp")
    try:
        if not have_ffmpeg():
            raise RuntimeError("ffmpeg isn't installed, so there's nothing to "
                               "build with. The page shows the one command.")
        rec = current_file()
        if not rec:
            raise RuntimeError("no recording loaded")
        src = os.path.join(WORK, rec)
        st = read_state()
        total = duration(src)
        start = parse_ts(st.get("start")) or 0.0
        end = parse_ts(st.get("end"))
        if end is None or end <= 0 or end > total:
            end = total
        if end - start < 1.0:
            raise RuntimeError("the start and end points are less than a "
                               "second apart — check them and try again")

        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)

        say("recording:  %s  (%s)" % (rec, hms(total)))
        say("trimming:   %s to %s  (%s between the marks)"
            % (hms(start), hms(end), hms(end - start)))

        cuts, bad = parse_cuts(st.get("cuts"), 0.0, total)
        for c in bad:
            say("   ! ignoring a cut row that doesn't make sense: %s to %s"
                % (c.get("from") or "?", c.get("to") or "?"))
        cuts = merge_spans(cuts)
        keep = keep_spans(start, end, cuts)
        if not keep:
            raise RuntimeError("the cuts remove everything between the trim "
                               "points — nothing would be left")

        removed = (end - start) - sum(b - a for a, b in keep)
        if cuts:
            say("cuts:       %d from the middle, %s in total"
                % (len(cuts), hms(removed)))
            for a, b, why in cuts:
                say("            %s to %s%s"
                    % (hms(a), hms(b), ("  (%s)" % why) if why else ""))

        # 1. Pull out each surviving piece and join them. Seeking before -i is
        # fast; re-encoding to wav is what makes it land on the exact sample
        # rather than the nearest keyframe.
        #
        # Every piece gets its own short fade before it's joined to the next,
        # because a cut in the middle of a sentence is exactly the place a
        # butt-join clicks.
        body_parts = []
        for i, (a, b) in enumerate(keep):
            raw_piece = os.path.join(tmp, "keep%02d-raw.wav" % i)
            run_ff(["-ss", "%.3f" % a, "-t", "%.3f" % (b - a), "-i", src,
                    "-ac", "2", "-ar", SR, "-c:a", "pcm_s16le", raw_piece],
                   "cutting piece %d" % (i + 1))
            piece = os.path.join(tmp, "keep%02d.wav" % i)
            fade_piece(raw_piece, piece, "fading piece %d" % (i + 1))
            body_parts.append(piece)

        body = os.path.join(tmp, "body.wav")
        body_seams = []
        if len(body_parts) == 1:
            shutil.copyfile(body_parts[0], body)
        else:
            at = 0.0
            for p in body_parts[:-1]:
                at += duration(p)
                body_seams.append(at)
            listing = os.path.join(tmp, "body.txt")
            with open(listing, "w", encoding="utf-8") as f:
                for p in body_parts:
                    f.write("file '%s'\n" % p.replace("'", "'\\''"))
            run_ff(["-f", "concat", "-safe", "0", "-i", listing, "-c", "copy",
                    body], "joining the pieces")
            say("            rebuilt from %d pieces" % len(body_parts))

        say("            %s removed in all, %s of episode left"
            % (hms(start + (total - end) + removed), hms(duration(body))))

        # 2. level
        if st.get("normalise", True):
            say("levelling:  measuring, then correcting to %s LUFS..." % TARGET_LUFS)
            m = measure_loudness(body)
            levelled = os.path.join(tmp, "level.wav")
            if m:
                run_ff(["-i", body, "-af",
                        "loudnorm=I=%s:TP=%s:LRA=%s:measured_I=%s:"
                        "measured_TP=%s:measured_LRA=%s:measured_thresh=%s:"
                        "offset=%s:linear=true"
                        % (TARGET_LUFS, TRUE_PEAK, LRA, m["input_i"],
                           m["input_tp"], m["input_lra"], m["input_thresh"],
                           m.get("target_offset", "0.0")),
                        "-ar", SR, levelled], "levelling")
                say("            was %s LUFS, now %s" % (m["input_i"], TARGET_LUFS))
            else:
                # One pass is worse than two, but far better than nothing, and
                # it only happens when ffmpeg's JSON can't be read.
                run_ff(["-i", body, "-af",
                        "loudnorm=I=%s:TP=%s:LRA=%s" % (TARGET_LUFS, TRUE_PEAK, LRA),
                        "-ar", SR, levelled], "levelling")
                say("            measured pass unavailable, used a single pass")
            body = levelled
        else:
            say("levelling:  skipped, levels left as they are")

        # 3. join
        pieces = []
        order = []
        seams = []
        intro, outro = music_path("intro"), music_path("outro")
        for tag, path in (("intro", intro), ("body", body), ("outro", outro)):
            if not path:
                continue
            order.append("episode" if tag == "body" else tag)
            wav = os.path.join(tmp, "%s-raw.wav" % tag)
            if tag == "body":
                shutil.copyfile(path, wav)
            else:
                to_wav(path, wav, "reading the %s" % tag)
            faded = os.path.join(tmp, "%s.wav" % tag)
            fade_piece(wav, faded, "fading the %s" % tag)
            pieces.append(faded)

        # Where every join lands in the finished file: the music joins, plus
        # each place a cut was taken out of the middle. The check clip is
        # built from these, so a seam missing here is a seam nobody hears
        # until it's published.
        at = 0.0
        for tag, p in zip(order, pieces):
            if tag == "episode":
                seams.extend(at + s for s in body_seams)
            at += duration(p)
            if p is not pieces[-1]:
                seams.append(at)
        seams = sorted(set(round(s, 2) for s in seams))

        joined = os.path.join(tmp, "joined.wav")
        if len(pieces) == 1:
            shutil.copyfile(pieces[0], joined)
        else:
            listing = os.path.join(tmp, "list.txt")
            with open(listing, "w", encoding="utf-8") as f:
                for p in pieces:
                    f.write("file '%s'\n" % p.replace("'", "'\\''"))
            run_ff(["-f", "concat", "-safe", "0", "-i", listing, "-c", "copy",
                    joined], "joining")

        # Read the order back off the pieces rather than describing it from
        # memory — a log that disagrees with the file is worse than no log,
        # because it's the thing you'd check to find out what went wrong.
        say("joining:    %s" % " + ".join(order))
        for tag, path in (("intro", intro), ("outro", outro)):
            if not path:
                say("            no %s is set, so none was added" % tag)

        # 4. encode
        base = os.path.splitext(rec)[0]
        out_name = "%s - trimmed.mp3" % base
        os.makedirs(OUT, exist_ok=True)
        out = os.path.join(OUT, out_name)
        tags = ["-metadata", "title=%s" % base]
        run_ff(["-i", joined, "-c:a", "libmp3lame", "-b:a", BITRATE,
                "-ar", SR, "-ac", "2"] + tags + [out], "encoding")

        # 5. the check clip: a few seconds either side of every join
        make_check(out, seams, tmp)

        st["output"] = out_name
        write_state(st)

        say("")
        say("Built %s" % out_name)
        say("  length   %s" % hms(duration(out)))
        say("  size     %.1f MB" % (os.path.getsize(out) / 1048576.0))
        say("")
        say("Play the check clip before you upload — it's a few seconds either")
        say("side of each join, back to back, so you hear whether it starts and")
        say("ends on the right word without listening to the whole thing.")
        JOB["ok"] = True
    except Exception as e:
        say("")
        say("Stopped: %s" % e)
        JOB["ok"] = False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        JOB["running"] = False
        JOB["done"] = True


def make_check(final, seams, tmp):
    """A short clip covering every join in the finished file.

    Almost everything that goes wrong with an edit is audible in the two
    seconds around a seam and inaudible everywhere else, so this is the whole
    quality check compressed into about twenty seconds.
    """
    total = duration(final)
    spans = []
    for s in seams:
        spans.append((max(0.0, s - 4.0), min(total, s + 4.0)))
    if not spans:
        spans = [(0.0, min(6.0, total)), (max(0.0, total - 6.0), total)]
    parts = []
    for i, (a, b) in enumerate(spans):
        if b - a < 0.5:
            continue
        p = os.path.join(tmp, "chk%d.wav" % i)
        run_ff(["-ss", "%.3f" % a, "-t", "%.3f" % (b - a), "-i", final,
                "-ac", "2", "-ar", SR, "-c:a", "pcm_s16le", p], "check clip")
        parts.append(p)
    if not parts:
        return
    listing = os.path.join(tmp, "chk.txt")
    with open(listing, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '%s'\n" % p.replace("'", "'\\''"))
    run_ff(["-f", "concat", "-safe", "0", "-i", listing, "-c:a", "libmp3lame",
            "-b:a", BITRATE, os.path.join(OUT, "_check.mp3")], "check clip")


# ------------------------------------------------------------------- page
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--navy:#22303C;--navy2:#2E404F;--teal:#5EA8A0;--teal-d:#4A8A83;
      --ink:#1A2530;--grey:#6B7A88;--line:#E4E9ED;--wash:#F5F7F8;--red:#C4443B}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
     color:var(--ink);background:var(--wash);line-height:1.6}
header{background:var(--navy);color:#fff;padding:16px 0}
.wrap{max-width:880px;margin:0 auto;padding:0 26px}
header b{font-size:17px;font-weight:600;letter-spacing:-.01em}
header small{color:#8FA9BC;margin-left:10px;font-size:12px}
h1{font-size:22px;font-weight:600;letter-spacing:-.02em;margin:30px 0 0}
h2{font-size:14px;font-weight:600;margin-bottom:14px}
.card{background:#fff;border:1px solid var(--line);border-radius:6px;
      padding:22px;margin-top:18px}
.muted{color:var(--grey);font-size:14px}
.hint{color:var(--grey);font-size:13px;line-height:1.55;margin-top:10px}
.btn{background:var(--navy);color:#fff;border:1px solid transparent;
     border-radius:4px;padding:9px 16px;font-size:14px;font-weight:500;
     cursor:pointer;font-family:inherit}
.btn:hover{background:var(--navy2)}
.btn:disabled{opacity:.45;cursor:default}
.btn.ghost{background:transparent;color:var(--ink);border-color:var(--line)}
.btn.ghost:hover{background:var(--wash)}
.btn.sm{padding:6px 12px;font-size:13px}
.bar{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:14px}
.drop{border:2px dashed var(--line);border-radius:6px;padding:36px 20px;
      text-align:center;background:var(--wash);cursor:pointer}
.drop.over{border-color:var(--teal);background:#EEF6F5}
.drop b{display:block;font-size:15px;margin-bottom:5px}
.drop span{font-size:13px;color:var(--grey)}
.scrub{background:var(--navy);color:#fff;border-radius:6px;padding:22px}
.scrub audio{width:100%;margin:14px 0;filter:invert(1) hue-rotate(180deg)}
.scrub .t{font-size:38px;font-weight:700;letter-spacing:-.03em;
          font-variant-numeric:tabular-nums;line-height:1.1}
.marks{display:flex;gap:30px;margin-top:10px;flex-wrap:wrap}
.marks div{font-size:12px;color:#A9C0D0}
.marks b{display:block;font-size:20px;color:var(--teal);
         font-variant-numeric:tabular-nums;font-weight:600}
.scrub .btn{background:var(--teal-d)}
.scrub .btn:hover{background:var(--teal)}
.scrub .btn.ghost{background:transparent;color:#fff;border-color:rgba(255,255,255,.28)}
.scrub .btn.ghost:hover{background:rgba(255,255,255,.08)}
.f{margin-top:14px}
.f label{display:block;font-size:12px;font-weight:600;color:var(--grey);
         margin-bottom:5px;letter-spacing:.02em}
.f input[type=text]{width:150px;padding:8px 10px;border:1px solid var(--line);
   border-radius:4px;font-size:14px;font-family:inherit;font-variant-numeric:tabular-nums}
.row{display:flex;gap:18px;flex-wrap:wrap}
.chk{display:flex;align-items:center;gap:8px;font-size:14px;cursor:pointer}
.warn{background:#FBEDEB;border:1px solid #E8C4BF;border-radius:6px;
      padding:14px 16px;margin-top:18px;font-size:14px;line-height:1.55}
.warn code{background:#fff;border:1px solid #E8C4BF;border-radius:3px;
           padding:2px 7px;font-size:13px}
.ok{background:#EDF6F4;border:1px solid #C6E2DC;border-radius:6px;
    padding:14px 16px;font-size:14px}
pre{background:var(--navy);color:#D8E4EC;border-radius:6px;padding:16px;
    font-size:12.5px;line-height:1.65;overflow:auto;max-height:340px;
    white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace}
.music{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
       padding:13px 0;border-bottom:1px solid var(--line)}
.music:last-child{border-bottom:0}
.music .who{width:58px;font-weight:600;font-size:14px}
.music .st{flex:1;font-size:14px;color:var(--grey);min-width:170px}
.pill{display:inline-block;background:var(--line);color:var(--grey);
      border-radius:20px;padding:2px 10px;font-size:11px;font-weight:600;
      letter-spacing:.04em;text-transform:uppercase}
.pill.on{background:#D9EEE9;color:#2C6B62}
.prog{height:4px;background:var(--line);border-radius:2px;margin-top:14px;
      overflow:hidden;display:none}
.prog i{display:block;height:100%;background:var(--teal);width:0}
audio.small{width:100%;margin-top:10px}
.cut{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:9px 0;
     border-bottom:1px solid var(--line)}
.cut input{padding:7px 9px;border:1px solid var(--line);border-radius:4px;
     font-size:13.5px;font-family:inherit}
.cut .c-from,.cut .c-to{width:96px;font-variant-numeric:tabular-nums}
.cut .c-why{flex:1;min-width:150px}
.retake{padding:11px 0;border-bottom:1px solid var(--line)}
.retake:last-child{border-bottom:0}
.rt{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.rt b{font-variant-numeric:tabular-nums;font-size:15px;min-width:64px}
.rq{color:var(--grey);font-size:13.5px;line-height:1.55;margin-top:5px}
"""

PAGE = """<!doctype html><meta charset="utf-8">
<title>Audio trimmer</title>
<style>__CSS__</style>
<header><div class="wrap"><b>Audio trimmer</b>
  <small>trim by ear, add your music, export</small></div></header>
<div class="wrap">
__FFMPEG__

<h1>1. The recording</h1>
<div class="card">
  <div class="drop" id="drop" onclick="pick()">
    <b id="dropb">__DROPTITLE__</b>
    <span>audio or video &mdash; mp3, m4a, wav, mp4, mov and the rest</span>
    <input type="file" id="fi" hidden>
  </div>
  <div class="prog" id="prog"><i id="bar"></i></div>
  <div class="muted" id="pmsg" style="margin-top:10px"></div>
  __CLEAR__
</div>

__TRIMMER__
__CUTS__

<h1>__MUSICSTEP__. Your intro and outro</h1>
<div class="card">
  __MUSICROWS__
  <p class="hint">Set once and used on every recording after this. Uploading
    again replaces what's there. Leave one empty and it's simply left off.</p>
</div>

<h1>__BUILDSTEP__. Build it</h1>
<div class="card">
  <label class="chk"><input type="checkbox" id="norm" __NORM__>
    Level the speech to &minus;16 LUFS</label>
  <p class="hint">What Apple and Spotify expect. Too quiet and people turn you
    up and get blasted by the next podcast; too loud and it distorts. Adds a
    minute or two on a long recording, because it measures the whole file
    before correcting it. Turn it off if your audio is already mastered.</p>
  <div class="bar">
    <button class="btn" id="go" onclick="build()" __CANBUILD__>Build the MP3</button>
    <span class="muted" id="gomsg"></span>
  </div>
  <pre id="out" style="display:none;margin-top:16px"></pre>
  __RESULT__
</div>

<p class="muted" style="margin:26px 0 40px">Everything happens on this
  computer. Nothing is uploaded anywhere.</p>
</div>
<script>
const g=id=>document.getElementById(id);
const fi=g('fi'), drop=g('drop');

function fmt(s){s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),
  m=Math.floor(s%3600/60),x=s%60;
  return h?h+':'+String(m).padStart(2,'0')+':'+String(x).padStart(2,'0')
          :m+':'+String(x).padStart(2,'0');}
function secs(t){if(!t)return null;const p=String(t).split(':').map(Number);
  if(p.some(isNaN))return null;return p.reduce((a,b)=>a*60+b,0);}

/* ---------- the recording ---------- */
function pick(){ fi.click(); }
fi.onchange=()=>{ if(fi.files[0]) send(fi.files[0]); };
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,ev=>{
  ev.preventDefault(); drop.classList.add('over');}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{
  ev.preventDefault(); drop.classList.remove('over');}));
drop.addEventListener('drop',ev=>{
  const f=ev.dataTransfer.files[0]; if(f) send(f); });

function send(file){
  const prog=g('prog'), bar=g('bar'), msg=g('pmsg');
  prog.style.display='block'; msg.style.color=''; msg.textContent='';
  const x=new XMLHttpRequest();
  x.open('POST','/api/upload');
  x.setRequestHeader('X-Filename',encodeURIComponent(file.name));
  x.upload.onprogress=e=>{ if(e.lengthComputable){
    const pc=Math.round(100*e.loaded/e.total);
    bar.style.width=pc+'PCT';
    msg.textContent=file.name+'  '+pc+'PCT'; } };
  x.onload=()=>{
    let j={}; try{j=JSON.parse(x.responseText);}catch(e){}
    if(x.status===200&&j.ok){ location.reload(); }
    else{ prog.style.display='none'; msg.style.color='var(--red)';
          msg.textContent=j.error||('upload failed ('+x.status+')'); }
  };
  x.onerror=()=>{ prog.style.display='none'; msg.style.color='var(--red)';
    msg.textContent='upload failed'; };
  x.send(file);
}

async function clearFile(){
  if(!confirm('Remove this recording and start over?')) return;
  await fetch('/api/clear',{method:'POST'}); location.reload();
}

/* ---------- trimming by ear ---------- */
const au=g('au');
if(au){
  au.addEventListener('timeupdate',()=>{ g('cur').textContent=fmt(au.currentTime); });
  au.addEventListener('loadedmetadata',()=>{ g('dur').textContent=fmt(au.duration); relen(); });
  au.addEventListener('error',()=>{ const b=g('auerr'); if(b) b.style.display='block'; });
  document.addEventListener('keydown',e=>{
    if(e.target.tagName==='INPUT') return;
    if(e.code==='Space'){ e.preventDefault(); au.paused?au.play():au.pause(); }
    if(e.code==='ArrowLeft'){ e.preventDefault(); nudge(e.shiftKey?-30:-5); }
    if(e.code==='ArrowRight'){ e.preventDefault(); nudge(e.shiftKey?30:5); }
  });
}
function nudge(d){ if(au) au.currentTime=Math.max(0,au.currentTime+d); }
function setMark(which){
  if(!au) return;
  const t=fmt(au.currentTime);
  g('f-'+which).value=t; g('m'+which).textContent=t; relen(); save();
}
function clearMark(which){
  g('f-'+which).value=''; g('m'+which).textContent='\\u2014'; relen(); save();
}
function jump(which){
  if(!au) return;
  const v=secs(g('f-'+which).value); if(v===null) return;
  au.currentTime=Math.max(0,v-2); au.play();
}
function relen(){
  const a=secs(g('f-start').value)||0;
  const b=secs(g('f-end').value);
  const total=au?au.duration:0;
  const end=(b===null||!b)?total:b;
  const el=g('mlen'); if(!el) return;
  el.textContent=(end&&end>a)?fmt(end-a):'\\u2014';
  const cut=g('mcut');
  if(cut&&total) cut.textContent=fmt(Math.max(0,a)+Math.max(0,total-end));
}
['f-start','f-end'].forEach(id=>{ const e=g(id); if(e){
  e.addEventListener('input',()=>{ const w=id.slice(2);
    const m=g('m'+w); if(m) m.textContent=e.value||'\\u2014'; relen(); });
  e.addEventListener('change',save); }});

async function save(){
  await fetch('/api/trim',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({start:(g('f-start')||{}).value||'',
                         end:(g('f-end')||{}).value||'',
                         normalise:g('norm').checked})});
}
g('norm').addEventListener('change',save);

/* ---------- cuts from the middle ---------- */
function cutRows(){ return [...document.querySelectorAll('#cuts .cut')]; }
function openCut(){ return cutRows().find(r=>!r.querySelector('.c-to').value.trim()); }

function refreshCutBtn(){
  const b=g('cutbtn'); if(!b) return;
  b.textContent = openCut() ? 'End the cut here' : 'Start a cut here';
}
function cutOpen(){
  if(!au) return;
  const t=fmt(au.currentTime), row=openCut();
  if(row){
    const from=secs(row.querySelector('.c-from').value)||0;
    if(au.currentTime<=from){
      g('cutmsg').textContent='the end of a cut has to come after its start';
      return; }
    row.querySelector('.c-to').value=t;
    g('cutmsg').textContent='cut set: '+row.querySelector('.c-from').value+' to '+t;
  }else{
    addCut(t);
    g('cutmsg').textContent='cut starts at '+t+' — play on and press again to end it';
  }
  refreshCutBtn(); saveCuts();
}
function cutFrom(at){
  if(au) au.currentTime=Math.max(0,at);
  addCut(fmt(at));
  g('cutmsg').textContent='cut starts at '+fmt(at)+
    ' — play to where the good take begins and press End the cut here';
  refreshCutBtn(); saveCuts();
  const box=g('cuts'); if(box) box.scrollIntoView({behavior:'smooth',block:'center'});
}
function cutRow(from){
  const d=document.createElement('div');
  d.className='cut';
  d.innerHTML='<input type="text" class="c-from" placeholder="from">'+
    '<span class="muted">to</span>'+
    '<input type="text" class="c-to" placeholder="to">'+
    '<input type="text" class="c-why" placeholder="what it is \\u2014 optional">'+
    '<button class="btn ghost sm" onclick="hearCut(this)">Hear it</button>'+
    '<button class="btn ghost sm" style="color:var(--red)" '+
    'onclick="dropCut(this)">Remove</button>';
  if(from) d.querySelector('.c-from').value=from;
  d.querySelectorAll('input').forEach(i=>i.addEventListener('change',saveCuts));
  return d;
}
function addCut(from){
  const box=g('cuts'); if(!box) return;
  box.appendChild(cutRow(from||''));
  refreshCutBtn();
}
function dropCut(btn){ btn.closest('.cut').remove(); refreshCutBtn(); saveCuts(); }
function hearCut(btn){
  if(!au) return;
  const a=secs(btn.closest('.cut').querySelector('.c-from').value);
  if(a===null) return;
  au.currentTime=Math.max(0,a-3); au.play();
}
async function saveCuts(){
  const cuts=cutRows().map(r=>({from:r.querySelector('.c-from').value.trim(),
                                to:r.querySelector('.c-to').value.trim(),
                                why:r.querySelector('.c-why').value.trim()}))
                      .filter(c=>c.from||c.to);
  const r=await fetch('/api/cuts',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({cuts})});
  const j=await r.json();
  const s=g('cutsum'); if(s) s.textContent=j.summary||'';
}
document.querySelectorAll('#cuts .cut input').forEach(i=>
  i.addEventListener('change',saveCuts));
refreshCutBtn();

function goTo(at){ if(au){ au.currentTime=Math.max(0,at-3); au.play(); } }

/* ---------- the retake finder ---------- */
function pickTranscript(){
  const f=document.createElement('input');
  f.type='file'; f.accept='.txt,.vtt,.srt,.json,.text,.md';
  f.onchange=()=>{ if(f.files[0]) sendTranscript(f.files[0]); };
  f.click();
}
function sendTranscript(file){
  const msg=g('tmsg');
  msg.style.color=''; msg.textContent='reading '+file.name+'\\u2026';
  const x=new XMLHttpRequest();
  x.open('POST','/api/transcript');
  x.setRequestHeader('X-Filename',encodeURIComponent(file.name));
  x.onload=()=>{
    let j={}; try{j=JSON.parse(x.responseText);}catch(e){}
    if(x.status===200&&j.ok) location.reload();
    else{ msg.style.color='var(--red)';
          msg.textContent=j.error||('could not read it ('+x.status+')'); }
  };
  x.onerror=()=>{ msg.style.color='var(--red)'; msg.textContent='could not read it'; };
  x.send(file);
}

/* ---------- music ---------- */
function pickMusic(which){
  const f=document.createElement('input');
  f.type='file'; f.accept='audio/*,video/*';
  f.onchange=()=>{ if(f.files[0]) sendMusic(which,f.files[0]); };
  f.click();
}
function sendMusic(which,file){
  const msg=g('m-'+which);
  msg.style.color=''; msg.textContent='uploading\\u2026';
  const x=new XMLHttpRequest();
  x.open('POST','/api/music/'+which);
  x.setRequestHeader('X-Filename',encodeURIComponent(file.name));
  x.upload.onprogress=e=>{ if(e.lengthComputable)
    msg.textContent=Math.round(100*e.loaded/e.total)+'PCT'; };
  x.onload=()=>{
    let j={}; try{j=JSON.parse(x.responseText);}catch(e){}
    if(x.status===200&&j.ok) location.reload();
    else{ msg.style.color='var(--red)';
          msg.textContent=j.error||('failed ('+x.status+')'); }
  };
  x.onerror=()=>{ msg.style.color='var(--red)'; msg.textContent='failed'; };
  x.send(file);
}
async function dropMusic(which){
  if(!confirm('Remove the '+which+'?')) return;
  const r=await fetch('/api/music/'+which,{method:'DELETE'});
  const j=await r.json();
  if(j.ok) location.reload();
}

/* ---------- build ---------- */
let poll=null;
async function build(){
  const b=g('go'), out=g('out'), msg=g('gomsg');
  await save();
  b.disabled=true; b.textContent='Building\\u2026';
  msg.textContent='this can take a few minutes on a long recording';
  out.style.display='block'; out.textContent='Starting\\u2026';
  const r=await fetch('/api/build',{method:'POST'});
  const j=await r.json();
  if(!j.ok){ out.textContent=j.error||'could not start';
             b.disabled=false; b.textContent='Build the MP3'; return; }
  poll=setInterval(async()=>{
    const s=await (await fetch('/api/build')).json();
    out.textContent=s.lines.join('\\n');
    out.scrollTop=out.scrollHeight;
    if(s.done){ clearInterval(poll); msg.textContent='';
      if(s.ok) location.reload();
      else{ b.disabled=false; b.textContent='Build the MP3'; } }
  },700);
}
</script>
"""


def esc(s):
    return (str(s if s is not None else "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def cut_row(i, c):
    c = c or {}
    return ("""<div class="cut" data-i="%d">
      <input type="text" class="c-from" value="%s" placeholder="from">
      <span class="muted">to</span>
      <input type="text" class="c-to" value="%s" placeholder="to">
      <input type="text" class="c-why" value="%s" placeholder="what it is — optional">
      <button class="btn ghost sm" onclick="hearCut(this)">Hear it</button>
      <button class="btn ghost sm" style="color:var(--red)"
              onclick="dropCut(this)">Remove</button>
    </div>""" % (i, esc(c.get("from", "")), esc(c.get("to", "")),
                 esc(c.get("why", ""))))


def retake_html(hits):
    """The candidates, each with the words around it.

    The words are the point. One of these recordings contains the sentence
    "if you wanna stop and say, hey, let's redo this, that's fine too", which
    matches every retake pattern there is and isn't a retake. Showing the
    quote settles it in about a second; hiding it behind a cleverness score
    would sometimes hide the real one instead.
    """
    if hits is None:
        return ""
    if not hits:
        return ('<p class="muted" style="margin-top:14px">Nothing in that '
                'transcript sounds like a retake.</p>')
    rows = []
    for h in hits:
        rows.append("""<div class="retake">
          <div class="rt"><b>%s</b>
            <button class="btn ghost sm" onclick="goTo(%.2f)">Jump here</button>
            <button class="btn ghost sm" onclick="cutFrom(%.2f)">Cut from here</button>
          </div>
          <div class="rq">&ldquo;%s&rdquo;</div>
        </div>""" % (esc(h["clock"]), h["at"], h["at"], esc(h["quote"])))
    return ("""<p class="muted" style="margin:16px 0 4px">%d moment%s worth a
            listen. Read the words &mdash; some of these will be people
            <i>talking about</i> retakes rather than doing one.</p>%s"""
            % (len(hits), "" if len(hits) == 1 else "s", "".join(rows)))


def render():
    st = read_state()
    rec = current_file()
    ok_ffmpeg = have_ffmpeg()

    ffmpeg_html = ""
    if not ok_ffmpeg:
        cmd, extra = ffmpeg_hint()
        ffmpeg_html = ("""<div class="warn"><b>ffmpeg isn't installed.</b>
          It does the actual audio work, so nothing can be built until it's
          there. It's free and it takes one command:<br><br>
          <code>%s</code>%s<br><br>Then restart this tool.</div>"""
                       % (esc(cmd), ("<br><br>" + esc(extra)) if extra else ""))

    # ---- the recording and the player
    if rec:
        total = duration(os.path.join(WORK, rec))
        drop_title = "Replace &mdash; drop another recording here"
        clear = ('<div class="bar"><span class="muted">Loaded: <b>%s</b> &middot; %s'
                 '</span><button class="btn ghost sm" style="margin-left:auto;'
                 'color:var(--red)" onclick="clearFile()">Remove</button></div>'
                 % (esc(rec), hms(total)))
        trimmer = """
<h1>2. Trim by ear</h1>
<div class="card">
  <div class="scrub">
    <audio id="au" src="/audio?t=__T__" preload="metadata" controls></audio>
    <div id="auerr" style="display:none;background:#5A2018;border-radius:4px;
         padding:12px 14px;margin-bottom:12px;font-size:13px;line-height:1.5">
      Your browser won't play this format, so it can't be scrubbed here. That
      doesn't stop anything &mdash; ffmpeg reads it either way. Type the trim
      points into the boxes below instead.
    </div>
    <div class="t"><span id="cur">0:00</span>
      <span style="font-size:17px;color:#7F9AAE;font-weight:400">
        of <span id="dur">__DUR__</span></span></div>
    <div class="marks">
      <div>Start<b id="mstart">__START__</b></div>
      <div>End<b id="mend">__END__</b></div>
      <div>Episode length<b id="mlen">&mdash;</b></div>
      <div>Being cut<b id="mcut">&mdash;</b></div>
    </div>
    <div class="bar">
      <button class="btn sm" onclick="setMark('start')">Set start here</button>
      <button class="btn sm" onclick="setMark('end')">Set end here</button>
      <button class="btn ghost sm" onclick="nudge(-5)">&minus;5s</button>
      <button class="btn ghost sm" onclick="nudge(5)">+5s</button>
      <button class="btn ghost sm" onclick="jump('start')">Hear the start</button>
      <button class="btn ghost sm" onclick="jump('end')">Hear the end</button>
    </div>
    <div class="bar" style="border-top:1px solid rgba(255,255,255,.14);
         padding-top:14px;margin-top:16px">
      <button class="btn sm" onclick="cutOpen()" id="cutbtn">Start a cut here</button>
      <span class="muted" id="cutmsg" style="color:#8FA9BC"></span>
    </div>
    <p class="hint" style="color:#8FA9BC">Play until the real opening line and
      press <b>Set start here</b>. Same at the end. Space bar plays and pauses,
      arrow keys move 5 seconds, hold shift for 30.</p>
  </div>
  <div class="row">
    <div class="f"><label>Start</label>
      <input type="text" id="f-start" value="__STARTV__" placeholder="0:00">
      <button class="btn ghost sm" style="margin-left:6px"
              onclick="clearMark('start')">clear</button></div>
    <div class="f"><label>End</label>
      <input type="text" id="f-end" value="__ENDV__" placeholder="end of file">
      <button class="btn ghost sm" style="margin-left:6px"
              onclick="clearMark('end')">clear</button></div>
  </div>
  <p class="hint">Leave the end empty to run to the end of the recording.
    You can type these instead &mdash; <code>1:30</code> and
    <code>00:01:30</code> and <code>90</code> all mean the same thing.</p>
</div>"""
        trimmer = (trimmer.replace("__T__", str(int(time.time())))
                   .replace("__DUR__", hms(total))
                   .replace("__START__", esc(st.get("start") or "\u2014"))
                   .replace("__END__", esc(st.get("end") or "\u2014"))
                   .replace("__STARTV__", esc(st.get("start") or ""))
                   .replace("__ENDV__", esc(st.get("end") or "")))
        cuts_html = """
<h1>3. Take out the middle</h1>
<div class="card">
  <p class="muted" style="margin-bottom:4px">For retakes, false starts, a
    phone going off &mdash; anything between the trim points that shouldn't
    be in the episode.</p>
  <p class="hint" style="margin-top:6px">Play up to where the bad bit starts
    and press <b>Start a cut here</b> on the player, then play to where the
    good take begins and press <b>End the cut here</b>. Add as many as you
    need. Times are in the original recording, the same as the player shows.</p>
  <div id="cuts">__CUTROWS__</div>
  <div class="bar">
    <button class="btn ghost sm" onclick="addCut()">Add a cut by hand</button>
    <span class="muted" id="cutsum">__CUTSUM__</span>
  </div>

  <div style="border-top:1px solid var(--line);margin-top:20px;padding-top:18px">
    <h2 style="margin-bottom:6px">Find the retakes for me</h2>
    <p class="muted" style="font-size:14px">Drop in the transcript and it lists
      every moment somebody asked to go again, with what they said. Beats
      scrubbing an hour of audio looking for them.</p>
    <div class="bar">
      <button class="btn ghost sm" onclick="pickTranscript()">Choose a transcript</button>
      <span class="muted" id="tmsg">__TNAME__</span>
    </div>
    <div id="retakes">__RETAKES__</div>
  </div>
</div>"""
        raw_cuts = st.get("cuts") or []
        rows = "".join(cut_row(i, c) for i, c in enumerate(raw_cuts))
        good, _bad = parse_cuts(raw_cuts, 0.0, total)
        good = merge_spans(good)
        summary = ("%d cut%s, %s in total"
                   % (len(good), "" if len(good) == 1 else "s",
                      hms(sum(b - a for a, b, _ in good)))) if good else ""
        cuts_html = (cuts_html.replace("__CUTROWS__", rows)
                     .replace("__CUTSUM__", summary)
                     .replace("__TNAME__", esc(st.get("transcript_name") or ""))
                     .replace("__RETAKES__", retake_html(st.get("retakes") or [])))
        music_step, build_step = "4", "5"
    else:
        cuts_html = ""
        drop_title = "Drop a recording here, or click to choose one"
        clear = ""
        trimmer = ""
        music_step, build_step = "2", "3"

    # ---- music rows
    rows = []
    for which in ("intro", "outro"):
        p = music_path(which)
        if p:
            state = ('<span class="pill on">set</span> &nbsp;<b>%s</b> &middot; %s'
                     % (esc(os.path.basename(p)), hms(duration(p))))
            buttons = ('<button class="btn ghost sm" onclick="pickMusic(\'%s\')">'
                       'Replace</button>'
                       '<button class="btn ghost sm" style="color:var(--red)" '
                       'onclick="dropMusic(\'%s\')">Remove</button>' % (which, which))
            player = ('<audio class="small" controls src="/music/%s?t=%d"></audio>'
                      % (which, int(time.time())))
        else:
            state = '<span class="pill">not set</span> &nbsp;nothing will be added'
            buttons = ('<button class="btn sm" onclick="pickMusic(\'%s\')">'
                       'Upload the %s</button>' % (which, which))
            player = ""
        rows.append('<div class="music"><div class="who">%s</div>'
                    '<div class="st">%s</div>%s'
                    '<span class="muted" id="m-%s"></span></div>%s'
                    % (which.title(), state, buttons, which, player))

    # ---- result
    out = output_path()
    result = ""
    if out:
        chk = check_path()
        result = """
    <div class="ok" style="margin-top:18px">
      <b>%s</b> &middot; %s &middot; %.1f MB
      <div class="bar" style="margin-top:12px">
        <a class="btn sm" href="/download">Download the MP3</a>
      </div>
      <audio class="small" controls src="/download?t=%d"></audio>
      %s
    </div>""" % (esc(os.path.basename(out)), hms(duration(out)),
                 os.path.getsize(out) / 1048576.0, int(time.time()),
                 ("""<div style="margin-top:14px;padding-top:14px;
                      border-top:1px solid #C6E2DC">
                     <b style="font-size:13px">The check clip</b>
                     <div class="muted" style="font-size:13px">A few seconds
                       either side of each join, back to back. If this sounds
                       right, the file is right.</div>
                     <audio class="small" controls src="/check?t=%d"></audio>
                   </div>""" % int(time.time())) if chk else "")

    page = (PAGE.replace("__CSS__", CSS)
            .replace("__FFMPEG__", ffmpeg_html)
            .replace("__DROPTITLE__", drop_title)
            .replace("__CLEAR__", clear)
            .replace("__TRIMMER__", trimmer)
            .replace("__CUTS__", cuts_html)
            .replace("__MUSICSTEP__", music_step)
            .replace("__BUILDSTEP__", build_step)
            .replace("__MUSICROWS__", "".join(rows))
            .replace("__NORM__", "checked" if st.get("normalise", True) else "")
            .replace("__CANBUILD__", "" if (rec and ok_ffmpeg) else "disabled")
            .replace("__RESULT__", result)
            .replace("PCT", chr(37)))
    return page


# ---------------------------------------------------------------- server
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/html; charset=utf-8"):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj), "application/json")

    def sent_filename(self):
        raw = urllib.parse.unquote(self.headers.get("X-Filename") or "")
        name = os.path.basename(raw.replace("\\", "/")).strip()
        return re.sub(r"[^A-Za-z0-9 ._-]", "_", name)

    def stream_to(self, path, length):
        """Write the body to disk in chunks rather than holding it in memory.

        A recording is tens of megabytes. It lands on a .part file first, so
        an upload that dies halfway can't be mistaken for a finished one.
        """
        tmp = path + ".part"
        got = 0
        try:
            with open(tmp, "wb") as f:
                while got < length:
                    chunk = self.rfile.read(min(1 << 20, length - got))
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
            if got != length:
                os.remove(tmp)
                return None, "the upload was cut short"
            os.replace(tmp, path)
        except Exception as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            return None, str(e)
        return got, None

    def serve_file(self, path, download=False):
        import mimetypes
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        size = os.path.getsize(path)
        rng = self.headers.get("Range")

        # Range support. Without it the browser can't seek in a long recording,
        # which is the entire point of this tool.
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                a = int(m.group(1)) if m.group(1) else 0
                b = int(m.group(2)) if m.group(2) else size - 1
                b = min(b, size - 1)
                if a > b:
                    return self.send(416, "bad range", "text/plain")
                if not m.group(2):
                    b = min(b, a + (4 << 20) - 1)
                length = b - a + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", "bytes %d-%d/%d" % (a, b, size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                try:
                    with open(path, "rb") as f:
                        f.seek(a)
                        left = length
                        while left > 0:
                            buf = f.read(min(1 << 18, left))
                            if not buf:
                                break
                            self.wfile.write(buf)
                            left -= len(buf)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if download:
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"'
                             % os.path.basename(path).replace('"', ""))
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                while True:
                    buf = f.read(1 << 18)
                    if not buf:
                        break
                    self.wfile.write(buf)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------- GET
    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        try:
            if p == "/":
                return self.send(200, render())
            if p == "/audio":
                rec = current_file()
                if not rec:
                    return self.send(404, "no recording", "text/plain")
                return self.serve_file(os.path.join(WORK, rec))
            m = re.match(r"^/music/(intro|outro)$", p)
            if m:
                path = music_path(m.group(1))
                if not path:
                    return self.send(404, "not set", "text/plain")
                return self.serve_file(path)
            if p == "/download":
                out = output_path()
                if not out:
                    return self.send(404, "nothing built yet", "text/plain")
                want = "t=" not in (urllib.parse.urlparse(self.path).query or "")
                return self.serve_file(out, download=want)
            if p == "/check":
                chk = check_path()
                if not chk:
                    return self.send(404, "no check clip", "text/plain")
                return self.serve_file(chk)
            if p == "/api/build":
                return self.json({"lines": JOB["lines"], "done": JOB["done"],
                                  "ok": JOB["ok"], "running": JOB["running"]})
            if p == "/favicon.ico":
                svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
                       '<rect width="64" height="64" rx="12" fill="#22303C"/>'
                       '<path d="M10 32h6l4-11 6 24 5-17 4 9h19" fill="none" '
                       'stroke="#5EA8A0" stroke-width="4" stroke-linejoin="round" '
                       'stroke-linecap="round"/></svg>')
                return self.send(200, svg, "image/svg+xml")
            return self.send(404, "not found", "text/plain")
        except Exception:
            import traceback
            return self.send(500, traceback.format_exc(), "text/plain")

    # ------------------------------------------------------------ POST
    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        try:
            if p == "/api/upload":
                name = self.sent_filename()
                if not name:
                    return self.json({"ok": False, "error": "no filename"}, 400)
                if not name.lower().endswith(MEDIA_EXT):
                    return self.json({"ok": False, "error":
                                      "%s isn't an audio or video file" % name}, 400)
                os.makedirs(WORK, exist_ok=True)
                # One recording at a time — clear the last one and anything
                # built from it, so what's on the page is what's in the folder.
                clear_work()
                got, err = self.stream_to(os.path.join(WORK, name), n)
                if err:
                    return self.json({"ok": False, "error": err}, 400)
                write_state({"start": "", "end": "", "normalise": True,
                             "cuts": [], "retakes": None})
                return self.json({"ok": True, "name": name, "bytes": got})

            m = re.match(r"^/api/music/(intro|outro)$", p)
            if m:
                which = m.group(1)
                name = self.sent_filename()
                ext = os.path.splitext(name)[1].lower()
                if ext not in MEDIA_EXT:
                    return self.json({"ok": False, "error":
                                      "%s isn't an audio file" % (name or "that")},
                                     400)
                os.makedirs(MUSIC, exist_ok=True)
                # Clear the slot first, whatever extension was in it, or an
                # old intro.wav sits beside the new intro.mp3.
                for f in os.listdir(MUSIC):
                    if os.path.splitext(f)[0].lower() == which:
                        try:
                            os.remove(os.path.join(MUSIC, f))
                        except OSError:
                            pass
                got, err = self.stream_to(os.path.join(MUSIC, which + ext), n)
                if err:
                    return self.json({"ok": False, "error": err}, 400)
                return self.json({"ok": True, "name": which + ext, "bytes": got})

            if p == "/api/transcript":
                name = self.sent_filename() or "transcript.txt"
                raw = self.rfile.read(n) if n else b""
                if not raw:
                    return self.json({"ok": False, "error": "that file is empty"}, 400)
                text = raw.decode("utf-8", "replace")
                # A .docx or a PDF arrives as bytes that decode to noise. Say
                # so plainly rather than reporting no retakes, which reads as
                # "there are none" instead of "I couldn't read this".
                printable = sum(1 for ch in text[:4000] if ch.isprintable()
                                or ch in "\n\r\t")
                if printable < len(text[:4000]) * 0.85:
                    return self.json({"ok": False, "error":
                                      "that doesn't look like a text transcript "
                                      "— a .txt, .vtt, .srt or Rev.ai .json "
                                      "works"}, 400)
                st = read_state()
                st["transcript_name"] = name
                st["retakes"] = find_retakes(text)
                write_state(st)
                return self.json({"ok": True, "found": len(st["retakes"])})

            body = {}
            if n:
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except Exception:
                    body = {}

            if p == "/api/cuts":
                st = read_state()
                rows = body.get("cuts")
                st["cuts"] = [{"from": str((c or {}).get("from") or "").strip(),
                               "to": str((c or {}).get("to") or "").strip(),
                               "why": str((c or {}).get("why") or "").strip()}
                              for c in (rows or []) if isinstance(c, dict)]
                write_state(st)
                total = duration(os.path.join(WORK, current_file() or ""))
                good, bad = parse_cuts(st["cuts"], 0.0, total or None)
                good = merge_spans(good)
                summary = ""
                if good:
                    summary = ("%d cut%s, %s in total"
                               % (len(good), "" if len(good) == 1 else "s",
                                  hms(sum(b - a for a, b, _ in good))))
                if bad:
                    summary += ("%s%d row%s not usable yet"
                                % (" · " if summary else "", len(bad),
                                   "" if len(bad) == 1 else "s"))
                return self.json({"ok": True, "summary": summary})

            if p == "/api/trim":
                st = read_state()
                st["start"] = str(body.get("start") or "").strip()
                st["end"] = str(body.get("end") or "").strip()
                st["normalise"] = bool(body.get("normalise", True))
                write_state(st)
                return self.json({"ok": True})

            if p == "/api/clear":
                clear_work()
                write_state({})
                return self.json({"ok": True})

            if p == "/api/build":
                if JOB["running"]:
                    return self.json({"ok": False, "error": "already building"})
                if not current_file():
                    return self.json({"ok": False, "error": "no recording loaded"})
                JOB.update({"running": True, "lines": [], "done": False, "ok": False})
                threading.Thread(target=build, daemon=True).start()
                return self.json({"ok": True})

            return self.json({"ok": False, "error": "not found"}, 404)
        except Exception:
            import traceback
            return self.json({"ok": False, "error": traceback.format_exc()}, 500)

    # ---------------------------------------------------------- DELETE
    def do_DELETE(self):
        p = urllib.parse.urlparse(self.path).path
        m = re.match(r"^/api/music/(intro|outro)$", p)
        if not m:
            return self.send(404, "not found", "text/plain")
        which = m.group(1)
        gone = []
        for f in (os.listdir(MUSIC) if os.path.isdir(MUSIC) else []):
            if os.path.splitext(f)[0].lower() == which:
                try:
                    os.remove(os.path.join(MUSIC, f))
                    gone.append(f)
                except OSError as e:
                    return self.json({"ok": False, "error":
                                      "couldn't remove %s (%s)"
                                      % (f, e.strerror or e)})
        return self.json({"ok": True, "removed": gone})


def clear_work():
    """Empty the working folder, but never touch music/."""
    if not os.path.isdir(WORK):
        return
    shutil.rmtree(OUT, ignore_errors=True)
    for f in os.listdir(WORK):
        path = os.path.join(WORK, f)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(MUSIC, exist_ok=True)
    url = "http://127.0.0.1:%d" % PORT
    print("""
  Audio trimmer

    %s

  Everything runs on this computer. Press Ctrl-C to stop.
""" % url)
    if not have_ffmpeg():
        cmd, extra = ffmpeg_hint()
        print("  ffmpeg isn't installed yet, so nothing can be built.")
        print("  Install it with:\n\n      %s\n" % cmd)
        if extra:
            print("  %s\n" % extra)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        Server(("127.0.0.1", PORT), H).serve_forever()
    except KeyboardInterrupt:
        print("  stopped\n")


if __name__ == "__main__":
    main()
