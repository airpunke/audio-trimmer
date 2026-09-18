# Audio Trimmer

A small local tool for topping and tailing a podcast recording — cut the
chatter off both ends, take out retakes from the middle, add your intro and
outro, export the MP3.

It runs in your browser but entirely on your own machine. No account, no API
key, nothing uploaded anywhere. One Python file, no frameworks.

Built because the job is always the same — every recording starts with five
minutes of "can you hear me?" and ends with "did that work?" — and opening a
full DAW to remove them is more tool than the job needs.

## What it does

- **Trim by ear.** Play the recording in the page, hit *Set start here* when
  the real opening line begins, same at the end. Space bar plays and pauses,
  arrow keys scrub, shift-arrow jumps 30 seconds.
- **Cut from the middle.** For retakes, false starts, a phone going off. Mark
  as many as you need against the same player.
- **Remove dead air automatically.** Any silence over two seconds, leaving a
  short breath. Works on any recording.
- **Remove repeated words.** "I, I, I think" becomes "I think". Needs a
  transcript with per-word timings.
- **Find the retakes for you.** Drop in the session transcript and it lists
  every moment someone asked to go again, with what they said and a button to
  jump straight there.
- **Add your intro and outro.** Uploaded through the page, kept between
  recordings, used on everything you build afterwards.
- **Level the audio** to −16 LUFS, the loudness Apple and Spotify expect.
  Optional, on by default.
- **Export** an MP3 at 128 kbps, ready to upload.

## Automatic cleanup

Two optional passes, both off by default, both reported after the build so you
can see what they did rather than approve a list beforehand.

**Dead air** is found in the waveform, so it works on any recording with no
transcript at all. Anything over **two seconds** goes, leaving about half a
second so it still breathes. Two seconds rather than one is deliberate:
cutting every pause makes an interview sound like an advert.

**Repeated words** need a transcript that times every word — a **Rev.ai
JSON**. A Zoom `.txt` has one timestamp per speaker turn, so there is nothing
to cut against: you can hear the stutter perfectly well, but nothing in the
file says where the second "I" ends. The checkbox tells you which kind you've
loaded before you tick it.

It keeps the **last** attempt, the one that carries on into the sentence, and
leaves alone words people double on purpose — "no, no, no" is emphasis, and so
is "really, really". It only catches whole repeated words; a half-swallowed
"th- the" leaves no separate word in the transcript to cut.

On a real 54-minute episode this found **127 repeated words, 56 seconds** —
worth having, and small enough that it doesn't change how anybody sounds.

## Requirements

- **Python 3.7+**
- **ffmpeg** — does all the audio work. The page shows the one install command
  for your system if it's missing.

## Running it

Double-click `Audio Trimmer.command` or `Audio Trimmer.bat`, whichever your
computer will run. Your browser opens at `http://127.0.0.1:8772`.

Or from a terminal:

```
python3 trimmer.py
```

## How the build works

1. **Trim and cut** — everything outside your marks, everything inside any cut
   you made, and anything the automatic passes found. What's left is rejoined
   in a single ffmpeg pass, however many pieces that is.
2. **Level** — two-pass loudnorm to −16 LUFS. It measures the whole file
   before correcting, so it takes a minute or two on a long recording.
3. **Join** — intro, episode, outro.
4. **Encode** — MP3, 128 kbps, 48 kHz, stereo.

Every join gets a **30 ms fade**. Butt-joining two pieces of audio leaves a
step in the waveform, and a step is an audible click — most obvious on a cut
made mid-sentence.

## The check clip

Every build also produces a short second file: a few seconds either side of
*every* join, back to back — where the intro meets the episode, each cut in
the middle, and where the outro comes in.

Almost everything that goes wrong with an automatic edit is audible in the two
seconds around a seam and inaudible everywhere else. So a 50-minute episode
gets verified in about fifteen seconds.

With automatic cleanup on there can be a hundred seams, so it keeps the first
and last — the music joins, the ones most likely to be wrong — and samples
across the rest. Otherwise the "check" would be seventeen minutes long and
nobody would play it.

## Notes

**The retake finder shows rather than decides.** It lists candidates with the
sentence around them instead of filtering to what it thinks are real retakes.
One test transcript contains *"if you wanna stop and say, hey, let's redo
this, that's fine too"* — matches every pattern going, and isn't a retake.
Anything clever enough to drop that would sometimes drop a real one, and then
you're scrubbing an hour of audio wondering why it found nothing. Reading a
dozen words settles it instantly.

It also only finds retakes somebody *announced*, and its timestamps point at
the start of the line the phrase appears in — the right neighbourhood, not the
exact frame. Your ears do the last bit.

**Formats.** Anything ffmpeg reads goes in: mp3, m4a, wav, flac, aiff, ogg,
opus, mp4, mov, webm and more. If your browser can't play a format back the
page says so and you type the trim points instead — the build works either
way.

**One recording at a time.** Deliberately. There's no library to manage and
nothing to name.

## Layout

```
trimmer.py        the whole tool
music/            your intro and outro (not in this repo)
work/             the recording being worked on (not in this repo)
work/_out/        the finished MP3 and check clip
```

`music/` and `work/` are gitignored — they hold your audio, not the program.
