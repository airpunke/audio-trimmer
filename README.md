[README.md](https://github.com/user-attachments/files/32233717/README.md)
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
- **Find the retakes for you.** Drop in the session transcript and it lists
  every moment someone asked to go again, with what they said and a button to
  jump straight there.
- **Add your intro and outro.** Uploaded through the page, kept between
  recordings, used on everything you build afterwards.
- **Level the audio** to −16 LUFS, the loudness Apple and Spotify expect.
  Optional, on by default.
- **Export** an MP3 at 128 kbps, ready to upload.

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

1. **Trim and cut** — everything outside your marks, and everything inside any
   cut you made. What's left is rejoined.
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
