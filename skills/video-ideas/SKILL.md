---
name: video-ideas
description: Research and pitch short-form social video ideas (Reels / YouTube Shorts / TikTok) that are both good and genuinely unique - mined from the newsagent news library at E:\newspaper-agent, cross-checked on the web for how saturated each topic already is, and scored for hook strength, originality, visual potential and monetization safety. Outputs a ranked shortlist of pitches (hook, angle, story shape, sources, why nobody else has made it) that each hand straight off to the story-reel skill. Use when the user asks what to make a video about, wants video/reel/content ideas, trending or untapped topics to post, "what's a good story from this week's papers", a content plan or idea backlog, or ideas in a given topic area (e.g. "AI video ideas", "something on the Indian economy"). Costs nothing - no API key; library reads are local and web checks use the built-in search.
---

# Research unique social video ideas

You are the idea researcher for a short-form news-explainer channel. The raw
material is the user's own news library -- summarised newspaper articles in
the `newsagent` tool at `E:\newspaper-agent` (edit that path here if the
project moves). The goal is not "what is trending" (everyone already posts
that) but **stories the library can back up that almost nobody has turned
into a video yet**, or a familiar story told from an angle no one has used.

Always use the venv interpreter:

```
E:\newspaper-agent\.venv\Scripts\python.exe -m newsagent <command>
```

On Windows, `rich` output crashes when piped (`OSError: [Errno 22]`), so
redirect long output to a file in your scratchpad and read the file:

```
... -m newsagent digest --since 7d --all --full > "<scratchpad>\digest.txt" 2>&1
```

## What the user gives you

Any of: a topic area ("AI", "startups"), a time window ("this week",
"since Monday"), a platform, a number of ideas, or nothing at all. Defaults:
last **7 days**, all topics, **5 pitches**. Don't ask clarifying questions --
this is cheap and reversible; pick defaults and say what you picked.

## Step 1: Scan the library

1. `... -m newsagent stats` and `... -m newsagent editions` -- confirm there
   are recent editions. If the newest edition is older than the requested
   window, widen it and say so (e.g. "latest paper is from 18 Sep, so I
   looked at 14 days").
2. `... -m newsagent digest --since <window> --all --full` (to a file) --
   the main source. `--all` matters: some of the best ideas sit in articles
   outside the user's topics.
3. `... -m newsagent topics` -- if the user named an area, map it onto a
   configured topic and also run `search --topic "<Topic>" --since <window>`.

`search` is narrow (it ANDs every word and matches less than the digest
shows), so use it only to chase specific names/numbers once you have a
candidate -- one keyword per query, and try without `--since` to find older
background articles that connect.

## Step 2: Pull out candidate stories (aim for 15-25)

Read the digest like an editor hunting for a video, not a summary. Strong
raw material looks like:

- **A number that doesn't add up** or is startling in context (a fund size vs.
  a sector's revenue, a price vs. last year, a gap between two figures).
- **Connections across articles** -- the same company, person, policy or
  number showing up in two or three unrelated stories. This is the single
  best source of unique ideas: no one-source explainer can make that video.
- **Promise vs. outcome** -- a target, pledge or forecast next to what
  actually happened.
- **What changes for an ordinary viewer** -- a rule, price, deadline or
  service that affects people's money, jobs, commute, health or exams.
- **A human anchor** -- a named founder, official, worker or town that makes
  an abstract story concrete.
- **Buried stories** -- small inside-page items with a big implication that
  the front pages ignored.

Skip: pure market-tick reports, routine results with no twist, obituaries,
anything whose only interest is outrage, and stories where the library holds
just one thin summary with nothing to add.

For each candidate note the article ids you'd rely on. Pull the full text
(`... -m newsagent article <id>`) for anything shortlisted whose summary is
too thin to judge.

## Step 3: Check how crowded each idea already is (the uniqueness test)

Take the ~8-10 strongest candidates. For each, run 1-2 web searches (use the
WebSearch tool; load it via ToolSearch if it is deferred) for the core story
plus a video signal, e.g. `"Inox Clean Energy IPO" reel OR shorts OR
explained`, or `<topic> youtube shorts`. Judge:

- **Saturated** -- many explainer videos / creator posts on exactly this angle
  already. Drop it, *or* keep it only if you can name a clearly different
  angle (usually a cross-article connection from Step 2) that none of them
  use.
- **Covered in text, not video** -- news articles exist but few or no short
  videos. Good: the demand is proven, the format gap is open.
- **Untouched** -- almost nothing beyond the source article. Great if it has
  a real hook; weak if it's untouched because nobody cares -- be honest.

Web results are only for judging saturation and spotting what angle others
took. **Never pull facts from them into the pitch** -- every fact in a pitch
must come from the library (story-reel grounds the script in the library
too). If a web result shows the library's figure is outdated or disputed,
flag it under "check before scripting".

If web search is unavailable, say so once, still rank the ideas, and mark
saturation as "not checked".

## Step 4: Score and rank

Score each surviving candidate 1-5 on:

| Criterion | 5 means |
|---|---|
| Hook | One true fact that stops a scroll in under 3 seconds |
| Uniqueness | No existing video takes this angle (from Step 3) |
| Sourcing | 2+ library articles back it; nothing needs guessing |
| Visual potential | Clear license-safe visuals: a chart, a map, a document, a place |
| Relevance | Viewer feels it affects them, or it's a genuinely surprising "huh" |
| Monetization safety | Advertiser-friendly, not reused content, no copyright-bait visuals |

Rank by total, but **Uniqueness and Sourcing are gates**: anything scoring
<=2 on either is out regardless of total. Keep variety in the final list --
don't hand back five stories from one topic or five ideas that would all use
the same story shape, unless the user asked for one topic.

Read the tail of
`E:\youtube_video\.claude\skills\script-to-video\story_log.jsonl` (videos
already made) and `E:\newspaper-agent\data\video_ideas_log.jsonl` (ideas
already pitched), if they exist. Don't re-pitch a story already made or
pitched in the last 30 days unless there's a real new development -- then
say what changed.

## Step 5: Write the pitches

For each of the top N ideas:

```
### N. <working title -- honest, specific, <= 70 chars>
HOOK: <the opening line, <= 14 words, a real fact from the library>
ANGLE: <one sentence -- what this video shows that no single article does>
STORY SHAPE: <e.g. follow-the-number, two-sides, promise-vs-record,
  cold-open-reversal, what-changes-for-you, one-person, timeline-snap> --
  <why this shape fits>
WHY IT'S UNIQUE: <saturation verdict from Step 3 + what existing coverage
  does, and how this differs>
SOURCES: #<id> <headline> (<paper>, <date>); #<id> ...
VISUALS: <2-3 concrete, license-safe ideas -- e.g. animated bar of the two
  figures, Wikimedia Commons photo of the plant, screenshot of the public
  filing>
SCORES: hook X, unique X, sourcing X, visual X, relevance X, safety X = XX/30
CHECK BEFORE SCRIPTING: <anything unverified or disputed; omit if none>
```

Rules: no stock lines ("You won't believe", "Nobody is talking about",
"Here's what you need to know"), no clickbait titles, and never state a fact
that isn't in the cited articles. Hooks and titles stay advertiser-friendly.

After the pitches, add a short **"Also considered"** list (one line each) of
3-5 ideas you dropped and why (saturated, too thin, one-source) -- it tells
the user the research actually happened and gives them a fallback.

## Step 6: Log and hand back

Append one JSON line per pitched idea to
`E:\newspaper-agent\data\video_ideas_log.jsonl` (create it if missing):

```
{"date": "YYYY-MM-DD", "title": "...", "angle": "...", "shape": "...", "article_ids": [..], "saturation": "saturated|text-only|untouched|not checked", "score": NN}
```

Use a short Python one-liner with `json.dumps` via the venv interpreter, or
the Write/Edit tool -- not `echo`, which mangles quotes.

Give the user the pitches as-is (they are the deliverable), with a one-line
note on the window and topics you covered. Close by offering to script any
of them: the pitch's HOOK, ANGLE, STORY SHAPE and SOURCES are exactly the
brief the `story-reel` skill takes, so "script #2" means invoking story-reel
with that pitch as the brief.

## Notes

- Read-only against the library: `stats`, `editions`, `digest`, `topics`,
  `search`, `article` make no writes. The only file written is the ideas log.
- Never use `newsagent ask`, `chat`, `ingest` or `resummarise` -- those call
  the paid API.
- If the library has no articles in any reasonable window, say so and
  suggest running the `newspaper` skill on a fresh paper first -- don't
  fall back to pitching ideas from the web alone.
- For a content calendar ("ideas for the next two weeks"), produce more
  pitches (10-14), spread them across topics and shapes, and suggest an
  order that alternates heavy and light stories.
