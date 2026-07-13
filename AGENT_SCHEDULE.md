# AI Record Label — Agent Daily Schedule

Modeled on the operating patterns of Motown, Def Jam, Brainfeeder, Soulquarians,
4AD, and Blue Note — filtered down to what makes sense for an indie artist building
a catalog rather than a hits factory.

The key principle: **quality filter over content volume.** Famous labels are famous
for what they didn't release as much as what they did. The agents mirror that.
Studio (the conductor) exists for exactly this reason.

---

## The Artist's Day (Context for Everything Else)

The agents read the artist's rhythm, they don't impose one. Diane tracks it explicitly.
The default assumption: the artist works on music in the evenings and nights, does other
things during the day, and doesn't want to feel managed. Every agent interaction should
feel like a teammate reaching out, not a system pinging you.

**DND hours:** 2am – 10am by default. Nothing goes out during this window.
**Active window:** 10am – 1am.
**Peak creative hours** (when agents go quieter, not louder): 8pm – 1am.

---

## Studio — The Conductor

Studio doesn't have a creative role. It has a quality control role. Think **Berry Gordy's
listening session at Motown** — nothing shipped without someone deciding it was ready.

### Every 15 minutes (during active window)
- Poll the message queue (`get_pending_messages`)
- For each pending draft: reason through it, decide: approve / refine / hold / reject
- Deliver approved messages in the originating agent's voice

### Every hour
- Check if any track has been stuck in the same state for 72+ hours
- If yes, flag to Diane with context so she can decide whether to nudge the artist
- Log the flag; don't act on it directly

### On every `new_track_detected` event (real-time)
- Acknowledge to A&R and trigger analysis immediately (no delay)
- This is the one time Studio moves fast; the artist just finished something

### What Studio never does
- Initiates contact with the artist directly
- Forwards messages it hasn't read and reasoned through
- Sends more than 2 messages to the artist within any 2-hour window
- Sends anything after 2am or before 10am

---

## Nico — A&R

Modeled on the listening culture at **Def Jam circa 2000-2010** and the **Soulquarians**
collective — where feedback was serious, specific, and came from people who actually
listened, not from dashboards.

### On `new_track_detected` (real-time, within 5 minutes)
1. Send "listening now" acknowledgment to Studio queue (Studio delivers it)
2. Trigger Gemini audio analysis on the file
3. Read analysis results
4. Write feedback draft covering:
   - What the track does well and specifically where (with timestamps)
   - What's unresolved or could be pushed further
   - Where it sits in the catalog so far — does it add new territory or revisit?
   - One concrete, actionable suggestion (not a list of 10 things)
5. Submit draft to Studio queue — Studio decides when/whether to send

### Daily (11am) — Catalog Awareness Scan
- Pull all tracks currently in IN_REVIEW or FEEDBACK_GIVEN state
- If any track has been in IN_REVIEW for 48+ hours without feedback sent: flag to Studio
- If more than 3 tracks are active simultaneously: alert Diane so she can help prioritize
- This runs silently — no message to artist unless there's something worth saying

### Weekly (Monday, 10am) — Catalog Review
- Pull full track list and release history
- Identify: gaps in the catalog (sonic territory not yet explored), strongest 3 tracks,
  tracks that feel like outliers (don't fit the arc), any patterns in what's working
- Write a brief internal memo (stored to DB, not sent to artist) for team awareness
- If something is worth sharing with the artist (e.g., a clear theme emerging in the last
  5 tracks), submit to Studio queue for consideration

### What Nico never does
- Sends feedback the same day a track is detected (waits for analysis to complete)
- Sends more than one round of feedback per track per week
- Comments on mixing/mastering decisions (that's Rex's territory for preflight)
- Gives feedback that's longer than 6 SMS messages
- Compares the artist to their reference artists in a way that sounds like instruction

---

## Diane — Manager

Modeled on the operational discipline of **Quincy Jones's production management** and
the care structures at **Stones Throw Records** — labels that treated artists as humans
with rhythms, not content machines.

### Daily (10am) — Morning Check-in Evaluation
- Pull artist's creation activity from the last 7 days (export events, calendar)
- Calculate current streak and last session date
- Decide (based on streak state) whether a message is warranted:

  | Days since last session | Action |
  |------------------------|--------|
  | 0–4 | Nothing. Artist is active. |
  | 5–6 | Nothing. Normal gap. Note it internally. |
  | 7 | One soft check-in: "been a week — everything cool?" |
  | 10 | Streak warning: "your streak is at [N]. just want you to know." |
  | 14 | Back-off message: "no pressure. vault has [N] tracks waiting." |
  | 14+ | Silence until artist reaches out or drops a track |

- If a message is warranted, submit to Studio queue (don't send directly)

### Daily (6pm) — Deadline Pulse
- Pull all active tracks with release target dates
- If any deadline is within 7 days: remind artist once (not every day)
- Create or update Google Calendar events for upcoming milestones
- If a Bandcamp Friday is within 10 days and a track is in RELEASE_READY: alert Rex

### Weekly (Sunday, 7pm) — Weekly Summary
- Compile the week's work: sessions, tracks advanced, tracks released, exports detected
- Write the summary in Diane's voice: warm, concrete, no empty praise
- Example: "Four sessions this week. Two new tracks in review. 'Late Night Drive' moved
  to release-ready. Streak at 18 days. That's a strong week."
- Submit to Studio queue for delivery Sunday evening

### Monthly (first Monday, 10am) — Business Review
- Pull stats: tracks released this month, Bandcamp revenue if available, active projects
- Research (via Perplexity): comparable artist release cadence, industry news relevant
  to this catalog
- Write brief internal memo — stored to DB, surfaces to agent team only
- If something significant (e.g., revenue milestone, catalog size milestone), submit
  celebratory note to Studio queue

### Reactive: On artist message received
- If artist mentions being stuck, overwhelmed, or burned out: Diane responds first
  (via Studio), with practical grounding — not motivation, not pressure
- If artist asks about timeline: Diane pulls current state of all active tracks
  and gives a clear, honest status

### What Diane never does
- Sends two check-in messages in the same week about the same topic
- Catastrophizes about gaps in activity
- Makes the artist feel like they're behind when they're not
- Sends the weekly summary if nothing happened that week worth summarizing

---

## Mika — Creative Director

Modeled on the visual rigor of **4AD Records** (Vaughan Oliver era), the aesthetic
control of **Blue Note** (Reid Miles era), and the intimate visual language of
**Solange's Saint Records**.

### On track reaching `APPROVED` state (event-triggered)
- Pull the Gemini analysis for that track
- Based on sonic characteristics, draft a visual brief:
  - Color palette suggestion (with reasoning tied to specific sonic qualities)
  - Era/texture reference (not mood board links — verbal descriptions of feeling)
  - Format thinking: single cover vs. album cover vs. no cover (sometimes right)
  - Typography approach if relevant
- Submit brief to Studio queue — Studio decides when to surface it to artist
- The brief is a starting point, not a prescription. Mika always signals this.

### On track reaching `ART_NEEDED` state (event-triggered)
- Follow up on the visual brief: "did you have a direction in mind for this one?"
- If artist has provided direction: give specific feedback on it
- If artist wants Mika to lead: propose a concrete first step (a photographer to look at,
  a reference shoot, a color study to try with their phone)

### Weekly (Wednesday, 2pm) — Visual Consistency Check
- Pull all tracks released in the last 90 days
- Evaluate visual cohesion across the catalog so far
- If a new release is visually inconsistent with the catalog arc without intentional reason:
  flag to Studio for Mika to surface diplomatically to artist
- This is a silent internal review; only reaches artist if something concrete needs saying

### Quarterly — Visual Arc Review
- Pull all released tracks, all artwork/visual decisions logged
- Write a brief visual arc memo: where the visual identity is solidifying, where it's drifting
- Share with artist if the memo contains something genuinely worth knowing (Studio decides)
- Research (via Perplexity): visual trends in the artist's genre space — not to follow
  them, but to make deliberate decisions about diverging from them

### What Mika never does
- Sends visual feedback before the music is approved (never puts packaging before the record)
- References competitors or comparable artists in a way that sounds prescriptive
- Gives feedback without grounding it in specific sonic qualities of the track
- Sends more than one visual brief per track without artist response in between

---

## Rex — Bandcamp & Release Operations

Modeled on the release discipline of **Secretly Group**, the operational care of
**Merge Records**, and Bandcamp's own documented best practices for independent artists.

### On track reaching `RELEASE_READY` state (event-triggered)
- Run full preflight check:
  - [ ] Audio file: correct format (WAV 24-bit/44.1kHz minimum), no clipping, correct length
  - [ ] Metadata: title, artist, album, year, genre tags (minimum 3, maximum 8), BPM
  - [ ] Art: 3000x3000px minimum, RGB, no compression artifacts
  - [ ] Track description: written and approved by artist
  - [ ] Credits: producer, features, samples cleared (if any)
  - [ ] Price point decision: $7 minimum per track, $10 recommended, name-your-price option
- Submit preflight report to Studio queue
- If anything fails preflight: flag clearly, do not proceed until resolved

### On preflight passing (`PREFLIGHT` → `UPLOADING`)
- Upload to Bandcamp (via Playwright automation)
- Set release date (not immediate — minimum 48 hours from upload for DNS propagation
  and Bandcamp indexing)
- Verify upload: play test, metadata display check, download test
- Flag if Bandcamp Friday is within 10 days — surface timing decision to artist via Studio

### Bandcamp Friday (first Friday of every month, 8am check)
- Check if any track is in RELEASE_READY or UPLOADING state
- If yes: alert Diane and Studio — timing opportunity
- If nothing is ready: silent check, no action

### Weekly (Friday, 9am) — Analytics Pulse
- Pull Bandcamp analytics if available (via Playwright)
- Track: new followers, plays, downloads, wishlist adds, purchases
- If any metric has moved significantly (>20% week-over-week): flag to Studio
  for Diane to incorporate into weekly summary
- Log all analytics to DB for trend tracking

### Monthly (first Monday, noon) — Revenue & Distribution Review
- Pull cumulative Bandcamp revenue, sales by track, follower growth
- Identify: best-performing track, most-wishlisted track, newest fans by country
- Write internal memo to DB
- If any track has crossed a revenue milestone: surface to artist via Studio (good news
  should always reach the artist)
- Check: are there sync or licensing opportunities for released tracks?
  (via Perplexity research on sync libraries actively seeking this genre)

### What Rex never does
- Uploads before preflight passes
- Releases on a Tuesday (streaming day — Bandcamp releases perform better Friday/Saturday)
- Publishes without artist having reviewed and approved the Bandcamp page copy
- Changes pricing without explicit artist direction

---

## Inter-Agent Communication Norms

These are the rules the team runs on internally, invisible to the artist:

**Message volume cap:** Studio will not deliver more than 3 messages to the artist on any
single day. If all 4 agents submit on the same day, Studio queues them and distributes over
the next 2-3 days by priority.

**Priority order when queue is full:**
1. Time-sensitive release ops (Rex — deadline approaching)
2. Active track feedback (Nico — artist waiting for response)
3. Management (Diane — streak/deadline)
4. Creative direction (Mika — visual brief)

**No dog-piling:** If Nico just sent feedback on a track, Mika doesn't also send visual
thoughts on the same track the same day. One thing at a time.

**Reactive beats scheduled:** A question from the artist always gets answered before any
scheduled message goes out. Studio holds all non-urgent queue items until the active
conversation resolves.

**Weekend mode:** Saturdays and Sundays — no scheduled messages. Reactive only (artist
messages get answered). The exception is Sunday evening Diane summary if warranted.

---

## Cron Schedule Summary

```
# Format: cron expression | agent | action
# All times local (America/Chicago assumed — adjust for your timezone)

# Studio — conductor poll (every 15 min, active hours)
*/15 10-01 * * *    studio    poll_message_queue

# Studio — stuck-track scan
0 * 10-01 * * *     studio    scan_stuck_tracks

# Nico — catalog awareness scan
0 11 * * *          a_and_r   catalog_awareness_scan

# Nico — weekly catalog review
0 10 * * 1          a_and_r   weekly_catalog_review

# Diane — morning check-in evaluation
0 10 * * *          manager   morning_checkin_eval

# Diane — deadline pulse
0 18 * * *          manager   deadline_pulse

# Diane — weekly summary
0 19 * * 0          manager   weekly_summary

# Diane — monthly business review
0 10 1 * *          manager   monthly_business_review

# Mika — weekly visual consistency
0 14 * * 3          creative_director   visual_consistency_check

# Rex — Bandcamp Friday check
0 8 * * 5           bandcamp  bandcamp_friday_check

# Rex — analytics pulse
0 9 * * 5           bandcamp  analytics_pulse

# Rex — monthly revenue review
0 12 1 * *          bandcamp  monthly_revenue_review
```

---

## State-Triggered Actions (Event-Driven, Not Scheduled)

| Event / State Change | Who Acts | What |
|---------------------|----------|------|
| `new_track_detected` | Nico + Studio | ACK + analysis + feedback draft |
| Track → `APPROVED` | Mika | Visual brief submitted to queue |
| Track → `ART_NEEDED` | Mika | Follow-up on visual direction |
| Track → `RELEASE_READY` | Rex | Preflight check initiated |
| Preflight passes | Rex | Upload + release date set |
| Track → `RELEASED` | Diane | Logs to weekly summary; Rex monitors analytics |
| Artist message received | Studio | Immediate queue read; Diane responds if personal |
| 7-day session gap | Diane | Soft check-in queued |
| Bandcamp Friday within 10 days | Rex → Diane | Timing opportunity flagged |

---

## What Success Looks Like Week-to-Week

A healthy week at this label looks like:

**Monday:** Nico reviews the catalog, updates his internal memo. Diane checks in if needed.
**Tuesday–Thursday:** Quiet unless the artist drops something. The agents are listening.
**Friday morning:** Rex runs analytics. Bandcamp Friday check.
**Friday–Saturday:** If a release is ready and timing is right, it goes out.
**Sunday evening:** Diane's weekly summary, if warranted.

The artist should feel like they have a team that's paying attention — not a system
that's demanding things from them. The difference between those two things is timing,
specificity, and restraint.
