# Write-up

**Model: `google/gemini-3.5-flash-lite`** (OpenRouter). Python 3.10+, standard library only.
`./run.sh <input_dir> <output.json>`.

## What I focused on, and why

The scoring decided the priorities. Open loops and help are 38 of the 74 checked points, and most of
their rules are asymmetric: a false "done" costs a point and a missed one costs nothing; a wrong
assist/remind where Arjun wanted "leave" costs a point and "leave" never does. So I spent most of the
time on loops and on encoding those asymmetries in code, not in prompts. Second came robustness: the
hidden set is 3x bigger, with every name changed, and has a hard 30-minute limit, so nothing may depend
on dev names, and a slow or failing model must never cost the whole run.

## My approach

Rules first (free), then four small model passes, each checked by code.

1. **Rules** (`xpert/structure.py`). Organisations from the seed, from group names that mention them,
   from "<brand> operations/team" groups (a company not in the seed), from groups made mostly of a
   company's team, and from household groups. Every chat goes to an organisation or one person.
   Circles come from a ladder of signals: the seed, then kinship or staff words in contact names (how
   Arjun saved them), then shared surname and group membership. Links are only for people Arjun deals with
   directly: a 1:1 chat, speaking in a small group of the organisation, or a tagged name of someone active
   (on dev, no tagged name that almost never spoke was linked in the answer).
2. **Circles, second opinion** (`people.py`). People the rules were unsure about get a ~600-character
   profile (saved names, shared groups, a sample of their 1:1 chat), about 12 per request. The model never
   overrides Arjun's own signals (the seed, a kinship tag, the spouse rule), and someone caught by his
   surname or a family group can be moved only within family (relative -> sibling, not relative -> client).
3. **Open loops** (`loops.py`). Each chat becomes compact lines (`M00391 14:57 Priya: ...`) with day
   headers. Long chats go in ~6,000-character parts in order, and each part is told which loops are still
   open, so a request on Monday can be closed by "paid" on Thursday. Small chats are packed together.
   Code accepts "done" only if the model names a real, later message in the same chat; everything else
   is "open". The prompt is strict about groups: only requests aimed at Arjun, promises made in a small
   group he is in, and birthdays/invitations. Only loops raised in the last two weeks are scored, so
   this pass reads only the last 16 days.
4. **Goals** (`goals.py`). Only Arjun's own messages, each with the message he replied to (~19% of the
   text), are read for goal hints. One final request merges them, rolling steps (tiles, windows) up into
   the bigger goal. At most 6 goals and 2 per area, best supported first, with up to 20 evidence ids each.
5. **Where to help** (`help.py`). Someone else's loop: "leave", in code. Arjun's own: an ordered
   checklist (feelings -> leave; throwaway line -> leave; his own decision/call/payment/work -> remind;
   something the assistant can prepare -> assist), with the step written before the action.

Every pass has a fallback (rules, no goals, "remind"), the client tolerates bad JSON, thinking blocks,
renamed fields and bare lists, and one hard deadline at 26 minutes stops new requests. Without a model
key the run still writes the rule-based parts in about a second.

**Dev score** (checker, 56 points): 27.9 (circles 8.5, organisations 4.1, chats 7.2, loops found 4.0,
loops closed 4.2). On the labelled example: 64 of 74, with organisations, chats, goals and help all full
marks; its loops parts move by a few points between runs (only 10 loops, mostly one request).

## What it costs

On dev, at OpenRouter prices ($0.30 in / $2.50 out per million tokens): **about $0.033 per 1,000
messages** (68,000 input and 5,000 output tokens per 1,000 messages; 105 requests; 5 to 6 minutes).
Where the input goes: loops 78%, goals 12%, circles 8%, help 3%. Instructions are repeated per request,
so small chats are packed to share them.

On the hidden set I expect **about $0.02 per 1,000 messages** (about $0.30 for the whole run), because
the loops pass reads only the last 16 of ~42 days. I chose flash-lite after comparing it with
gemini-3.5-flash on the biggest chat: flash judged status better but was 9x slower and ~5x the price. A
free Gemma model was too slow to finish in 30 minutes in my tests.

## Where it's wrong

Rough rates are from dev, which the organisers say is "good, not perfect", so treat them as estimates.

- **Loop source messages (largest loss).** For the same task we often name a different message than the
  answer: Arjun's reply instead of the request before it. I expect about a third of real loops to be
  found under the accepted message; loops found F1 around 0.25 to 0.35. A key labelled by hand may
  include many of our "extra" loops, which on dev are often real tasks.
- **Loops in long, busy chats** (the spouse chat holds over half of dev's loops): several small asks in a
  row get merged or skipped. Recall there is worse than in short chats.
- **Status.** About 1 in 10 matched loops is wrongly "done" (it costs a point each); about a quarter
  of real "done" loops stay "open" because the closing message is indirect (a photo, "ok").
- **Circles** about 70% right. Typical errors: parent or sibling read as "relative" when the
  surname is shared; colleagues with almost empty chats left as "friend"; client vs vendor for service
  businesses.
- **Organisations.** Seed companies, team groups and Home are found; organisations visible only in message
  content (a second company, a farmhouse) are missed, as are 1:1 chats that belong to an organisation (a
  contractor's chat about the house). Chats about 90% right.
- **Goals.** 4 to 6 goals; work and home goals are the most reliable. Goals stated only by others, or only
  in passing, are missed. I expect about half of ours to match.
- **Help.** About two-thirds of Arjun's open loops get "remind", a fifth "assist", about 12% "leave".
  Assist vs remind mix-ups cost nothing; the costly error is "remind" on something Arjun wanted left
  alone (small household logistics), which I expect for 1 in 8 of his loops.
- **Run to run**, results vary by about a point on each loop part even at temperature 0.
- **Assumptions.** The 16-day window assumes the scoring cut-off is close to "last two weeks". Under heavy
  rate limiting the deadline cuts the last loop requests short (seen once on the free tier: a valid
  output with a fifth of the loop requests missing).

AI use: built with Claude Code; the cleaned session log is in `ai-logs/` (data names and message text are
redacted by `tools/export_ai_log.py`).
