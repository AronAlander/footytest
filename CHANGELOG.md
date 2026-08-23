# What's new

Changes to the dashboard, newest first. Data updates every night at 03:15 UTC
and are not listed here — this is about what the site itself can do.

Entries normally come from the commits themselves, so this file does not have
to be kept up by hand. A commit opts in by ending its message with a trailer:

    Changelog: **Match reports.** What happened, and what we predicted.

Nightly data commits carry no trailer and never show up.

**This file overrides the commits, one whole date at a time.** A `## date`
heading here replaces everything the trailers said for that day — which is how
an entry gets fixed after the fact, since a trailer is frozen in git history.
To reword one entry, copy that date's entries here and edit them; to drop a
day entirely, give it a heading with a single entry saying whatever you want.
Dates that appear only here are added as normal, so a release note no commit
announced still works.

Run `python build_report.py --changelog` to see what the panel will say and
which source each date came from, before it goes out. The newest four dates
are rendered on the page; the rest stay here.

## 2026-08-23

- **A Matches tab.** Pick any upcoming fixture to see both clubs side by side: the model's win/draw/win call, recent form in results *and* in chance quality, past meetings, the venue split that applies to the match, and each squad's leading attackers.
- **Match reports.** Pick a match already played and see the score, what the chances were worth on the day, and the call this site published *before kickoff* — marked called it or missed against the actual result. A match that was never predicted says so rather than inventing an opinion after the fact.
- **Everything on the League tab is a way in.** Click a club's name for its style profile; click a fixture or a result row for the match.
- **A single club is now a profile.** Team comparison used to show nothing until you had picked two clubs. One club gives its style radar and league percentiles.
- **Overall record beside the venue split.** A club's home (or away) record now has its record across all matches underneath — a side that is excellent at home and ordinary overall is exactly what the venue row alone would hide.
- **Neutral head-to-head.** Past meetings are no longer coloured by whether the club that happens to be at home this time won them.
- **The open match survives a refresh.** The address bar now names it, so a reload comes back to the same match and the link can be shared.
- **This panel.** A running list of what has changed, so a returning visitor can tell at a glance whether anything is new.
- **Fixed:** Serie A's opening results briefly vanished from the published page on 23 August, after a rebuild from a stale local copy of the data overwrote them. The build now refuses to go quietly when the data it is about to publish is older than a day.
