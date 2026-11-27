"""Club-name matching shared by the report and the fetchers.

Three feeds name the same clubs three ways -- TheSportsDB says "Inter Milan",
Understat "Inter", FotMob "Inter" but also "Bayern München" where Understat
says "Bayern Munich". The report has always bridged fixtures to xG names with
this; fetch_fotmob.py now uses it too, to store the big five's FotMob rows
under Understat's names, so a club's history reads as one club across the
switch of source.
"""

import re
import unicodedata

# names normalisation alone cannot bridge, as (seen name -> wanted name).
# Extended when a build prints "no xG history matched", or when
# fetch_fotmob.py refuses a big-five club it cannot place
ALIASES = {
    "Inter Milan": "Inter",  # bare "Inter" is a subset of both Milan clubs
    "Borussia Mönchengladbach": "Borussia M.Gladbach",
    "Köln": "FC Cologne",
    "Hamburg": "Hamburger SV",
    "RB Leipzig": "RasenBallsport Leipzig",
    "Halmstad": "Halmstads BK",
    # and the same pair the other way round, for the squad bridge, which
    # joins FotMob's player table to its match table rather than to a
    # fixture list. "Halmstads" and "Halmstad" are different tokens once
    # "BK" is dropped, so normalisation alone leaves this club unlinked
    "Halmstads BK": "Halmstad",
    # FotMob's spellings of big-five clubs, onto Understat's
    "Bayern München": "Bayern Munich",
    "1. FC Köln": "FC Cologne",
    "Deportivo A Coruña": "Deportivo La Coruna",
    # TheSportsDB's spelling of the same club, unmatched since Deportivo
    # came up in August 2026, so none of its fixtures had a prediction
    "Deportivo de A Coruña": "Deportivo La Coruna",
}


def norm(name):
    """Club-name join key: ASCII-folded, lowercased, generic club tokens
    dropped, remaining tokens sorted so word order does not matter."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    drop = {
        "fc", "afc", "cf", "ac", "as", "ss", "ssc", "us", "ud", "cd", "rc",
        "rcd", "sc", "sd", "sv", "bsc", "vfl", "vfb", "tsg", "fsv", "spvgg",
        "if", "ff", "bk", "sk", "fk", "aif", "club", "de", "cp", "calcio",
        "1899", "1846", "1860", "04", "05", "09", "1", "96", "98", "99",
    }
    toks = [t for t in s.split() if t not in drop]
    return " ".join(sorted(toks)) or s.strip()


def mapping(seen_names, wanted_names):
    """Seen name -> wanted name (or None when nothing matches safely)."""
    by_norm = {norm(n): n for n in wanted_names}
    tokens = {n: set(norm(n).split()) for n in wanted_names}
    out = {}
    for name in seen_names:
        if name in ALIASES:
            out[name] = ALIASES[name]
            continue
        key = norm(name)
        if key in by_norm:
            out[name] = by_norm[key]
            continue
        want = set(key.split())
        subset = [n for n, t in tokens.items() if want <= t or t <= want]
        out[name] = subset[0] if len(subset) == 1 else None
    return out
