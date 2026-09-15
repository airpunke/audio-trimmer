# Audio trimmer

Top and tail a recording by ear, add your intro and outro, export the MP3.
One file at a time. That's the whole tool.

## Start it

Double-click the launcher in this folder — `Audio Trimmer.command` or
`Audio Trimmer.bat`, whichever one your computer will run. A console window
opens, then your browser at `http://127.0.0.1:8772`. Leave that window open
while you work; closing it stops the trimmer.

If you'd rather type it: `python3 trimmer.py` from this folder.

Everything runs on your computer. Nothing is uploaded anywhere.

## You need ffmpeg

It does the actual audio work, and there's no realistic way to do this
properly without it. The page says so if it's missing and shows the one
command for your system. Install it, restart the trimmer, and that's the last
you'll think about it.

## How it goes

**1. Drop the recording in.** Audio or video, almost any format — mp3, m4a,
wav, flac, aiff, ogg, opus, mp4, mov, webm and the rest. If your browser can't
play that format back it says so and you type the trim points instead; the
build works either way, because ffmpeg reads it regardless of what the browser
can do.

**2. Trim by ear.** Play until the real opening line and press **Set start
here**. Same at the end. Space bar plays and pauses, arrow keys move five
seconds, hold shift for thirty. **Hear the start** jumps two seconds before
your mark and plays, so you can check it without hunting for the spot again.

You can type the points instead — `1:30`, `00:01:30` and `90` all mean the
same thing. Leave the end empty to run to the end of the recording.

**3. Take out the middle.** For retakes, false starts, a phone going off.
Play up to where the bad bit starts, press **Start a cut here**, play on to
where the good take begins, press **End the cut here**. Add as many as you
need. Times are in the original recording — the same clock the player shows —
so a cut always means what you saw when you set it.

**Find the retakes for me.** Drop the session transcript in and it lists every
moment somebody asked to go again, with what they said, and a button to jump
there or start a cut. On a real recording it found the two retakes in a
54-minute session in about a second.

It shows you the words rather than deciding for you, on purpose. One real
transcript contains *"if you wanna stop and say, hey, let's redo this, that's
fine too"* — which matches every retake pattern going and isn't a retake. A
filter clever enough to drop that is also clever enough to drop the real one,
and then you're scrubbing an hour of audio wondering why it found nothing.
Reading twelve words settles it instantly.

Two things it can't do for you. The timestamp is the start of the line the
phrase appears in, so it's the right neighbourhood rather than the exact
frame — in one real case the retake happens midway through a 72-second turn,
so the number gets you there and your ears do the last bit. And it can only
find retakes somebody *announced*; a silent restart leaves no trace in the
words.

**4. Set your intro and outro.** Uploaded on the page, kept in `music/` next
to this file. Set once and used on every recording after that. Uploading
again replaces what's there; leave a slot empty and nothing is added.

**5. Build.** You get `<your file> - trimmed.mp3`, with a download button.

## What the build actually does

1. **Trim and cut** — everything before your start point, after your end
   point, and inside any cut you marked. What's left is rejoined, each piece
   with its own fade.
2. **Level** — the speech is normalised to −16 LUFS, which is what Apple and
   Spotify expect. Too quiet and people turn you up and get blasted by the
   next podcast; too loud and it distorts. It measures the whole file first
   and then corrects, which is why it takes a minute or two on a long
   recording. Uncheck it if your audio is already mastered.
3. **Join** — intro, then the episode, then outro
4. **Encode** — MP3 at 128 kbps, 48 kHz, stereo

Every join gets a **30 millisecond fade** — the music joins and every cut you
took out of the middle. Butt-joining two pieces of audio puts a step in the
waveform, and a step is an audible click; a cut mid-sentence is exactly where
that shows up.

Overlapping cuts are merged rather than applied twice, cuts given out of order
are sorted, and a cut that would leave nothing refuses to build. A row that
doesn't make sense — an end before its start — is reported in the log rather
than skipped quietly, because a cut you typed that silently didn't happen is
worse than one that complains.

## The check clip

After a build you get a second short file: a few seconds either side of
**every** join, back to back — where the intro meets the episode, every cut
you made in the middle, and where the outro comes in. Play it.

Almost everything that goes wrong with an edit is audible in the two seconds
around a seam and inaudible everywhere else, so this is the whole quality
check compressed into about fifteen seconds. It's the difference between
trusting the file and hoping.

## Where things are

```
trimmer.py        the whole tool, one file
music/            your intro and outro, kept between recordings
work/             the recording you're working on
work/_out/        the finished MP3 and the check clip
```

Finished files live in their own folder deliberately. When they sat beside the
recording, the MP3 from one build looked like a recording to the next one and
got trimmed again — `My Episode - trimmed - trimmed - trimmed.mp3`. It
compounds silently, and the filename is the only clue.

**Remove** clears the recording and starts over. It never touches `music/`.
