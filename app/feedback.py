"""Plain, rule-based feedback on a resume.yaml draft: things any reader can see are off (a job with no dates, a summary that runs
on, thirty skills). No AI and no key involved, so it is instant, free, and the page can re-run it on every keystroke. Judgment
calls (a bullet that is really company marketing, a list that says three and names five) are the AI review's job
(generate.review_resume). Every suggestion is {level, where, text, find}: level "fix" is something that will print wrong or break, "tip"
is advice, and `find` is a few words of the YAML the suggestion is about, so the page can jump to them when it is clicked. Nothing
here changes the draft."""
import re
from datetime import date
from typing import List, Optional

SUMMARY_WORDS = 120  # past this a summary stops being read
BULLET_CHARS = 350  # a bullet this long is a paragraph
MAX_BULLETS = 8  # per job
SKILL_GROUP_ITEMS = 15
SKILLS_TOTAL = 40
MAX_SUGGESTIONS = 30
MAX_OVERLAPS = 3  # named one by one; the rest are counted
OVERLAP_MONTHS = 1  # jobs may share this many months (one ends the month the next starts) before it is called an overlap

# The plain form a bullet starts with when it is written about a job someone STILL holds ("Lead a team", "Own the roadmap"). In a job
# that has ended the same bullet should be past tense ("Led", "Owned"). Only verbs whose present and past differ are listed, so "Set",
# "Run", "Cut" and the like (the same in both, or ambiguous) never raise a false alarm.
PRESENT_VERBS = frozenset("""
act advise analyze architect automate build collaborate consult coach coordinate create define deliver design develop direct drive
enable establish evaluate execute grow guide help identify implement improve integrate introduce launch lead maintain manage mentor
migrate oversee optimize own partner plan provide reduce resolve scale ship spearhead streamline support train transform unify work write
""".split())

MONTHS = {m: n for n, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def _item(level: str, where: str, text: str, find: str = "") -> dict:
    return {"level": level, "where": where, "text": text, "find": find}


def _months(value, today: date) -> Optional[int]:
    """A resume date ("Mar 2019", "March 2019", "2019", "03/2019", "2019-03", Present) as a month count, or None if it is not one
    this understands (the check that needed it is then simply skipped). A bare year means January."""
    s = _text(value).lower()
    if s in ("present", "current", "now"):
        return today.year * 12 + today.month - 1
    m = re.fullmatch(r"(?:(\d{1,2})[/-])?(\d{4})(?:-(\d{1,2}))?", s)
    if m:
        year, month = int(m.group(2)), int(m.group(1) or m.group(3) or 1)
        return year * 12 + month - 1 if 1 <= month <= 12 else None
    m = re.fullmatch(r"([a-z]{3})[a-z]*\.?,?\s+(\d{4})", s)
    if m and m.group(1) in MONTHS:
        return int(m.group(2)) * 12 + MONTHS[m.group(1)] - 1
    return None


def _first_word(bullet: str) -> str:
    m = re.match(r"[^A-Za-z]*([A-Za-z]+)", bullet)
    return m.group(1).lower() if m else ""


def _is_present_tense(bullet: str) -> bool:
    word = _first_word(bullet)
    return word in PRESENT_VERBS or (word.endswith("s") and word[:-1] in PRESENT_VERBS) or (word.endswith("es") and word[:-2] in PRESENT_VERBS)


def check_resume(data: dict, today: Optional[date] = None) -> List[dict]:
    """Suggestions for an already-parsed resume (a dict with a `basics` section). Most important first: fixes, then tips."""
    today = today or date.today()
    out: List[dict] = []
    basics = data.get("basics") if isinstance(data.get("basics"), dict) else {}

    if not _text(basics.get("email")):
        out.append(_item("fix", "Contact", "There is no email address. A reader who likes the resume needs a way to reach you.", "basics:"))
    if not _text(basics.get("phone")):
        out.append(_item("tip", "Contact", "There is no phone number. Many readers still expect one; leave it off only on purpose.", "basics:"))
    if not _text(basics.get("title")):
        out.append(_item("tip", "Header", "There is no headline title under your name (for example 'Senior Solutions Architect').", "basics:"))

    summary = _text(basics.get("summary"))
    words = len(summary.split())
    if not summary:
        out.append(_item("tip", "Summary", "There is no summary. Two to four sentences saying who you are and what you are best at is what most readers read first.", "basics:"))
    elif words > SUMMARY_WORDS:
        out.append(_item("tip", "Summary", f"The summary is {words} words. Readers skim: two to four sentences (about 60 to 90 words) get read, a long paragraph usually does not.", "summary:"))

    jobs = [j for j in (data.get("experience") or []) if isinstance(j, dict)]
    shown = [j for j in jobs if j.get("include_in_resume", True)]
    present = [j for j in shown if _text(j.get("end")).lower() in ("present", "current", "now")]
    if len(present) > 1:
        names = ", ".join(_text(j.get("company")) or "(no company)" for j in present)
        out.append(_item("tip", "Experience", f"{len(present)} jobs are marked Present ({names}). That is right only if you really hold them all at once.", _text(present[0].get("company"))))

    with_numbers = total_bullets = 0
    printed = [summary]  # everything that prints as prose, for the confidential-client check
    names = {}  # id(job) -> how the suggestions call it
    for number, job in enumerate(shown, 1):
        name = " at ".join(p for p in (_text(job.get("title")), _text(job.get("company"))) if p) or f"Job {number}"
        names[id(job)] = name
        anchor = _text(job.get("company")) or _text(job.get("title"))
        if not _text(job.get("title")):
            out.append(_item("fix", name, "This job has no title, so it will print with a blank title line.", anchor or "experience:"))
        if not _text(job.get("company")):
            out.append(_item("fix", name, "This job has no company.", anchor or "experience:"))
        if not _text(job.get("start")):
            out.append(_item("fix", name, "This job has no start date.", anchor))
        if not _text(job.get("end")):
            out.append(_item("fix", name, "This job has no end date, so it prints open-ended. If you still work there, write Present.", anchor))
        start, end = _months(job.get("start"), today), _months(job.get("end"), today)
        if start is not None and end is not None and end < start:
            out.append(_item("fix", name, f"This job ends ({_text(job.get('end'))}) before it starts ({_text(job.get('start'))}).", anchor))
        bullets = [_text(b) for b in (job.get("bullets") or []) if _text(b)]
        printed += bullets
        if not bullets:
            out.append(_item("tip", name, "This job has no bullets, so it prints as just a title and dates.", anchor))
        if len(bullets) > MAX_BULLETS:
            out.append(_item("tip", name, f"This job has {len(bullets)} bullets. Past about {MAX_BULLETS} readers stop reading; keep the strongest and drop the rest.", anchor))
        seen = set()
        for bullet in bullets:
            key = re.sub(r"\W+", " ", bullet.lower()).strip()
            if key in seen:
                out.append(_item("fix", name, f"This bullet appears twice: \"{bullet[:90]}\"", bullet[:40]))
            seen.add(key)
            if len(bullet) > BULLET_CHARS:
                out.append(_item("tip", name, f"A bullet is {len(bullet)} characters long, which is a paragraph. Split it or cut it to the result: \"{bullet[:70]}...\"", bullet[:40]))
            total_bullets += 1
            with_numbers += bool(re.search(r"\d", bullet))
        if end is not None and _text(job.get("end")).lower() not in ("present", "current", "now"):  # a job that has ended
            wrong = [b for b in bullets if _is_present_tense(b)]
            if wrong:
                out.append(_item("tip", name, f"This job has ended, but {len(wrong)} of its bullets {"is" if len(wrong) == 1 else "are"} in present tense (\"{wrong[0][:50]}...\"). Past tense (Led, Built, Owned) fits a job you no longer hold.", wrong[0][:40]))
    if total_bullets >= 5 and with_numbers / total_bullets < 0.25:
        out.append(_item("tip", "Experience", f"Only {with_numbers} of your {total_bullets} bullets contain a number. Readers trust results they can measure (a percentage, a count, money, a time saved); add one where you know it.", "experience:"))

    # Jobs that overlap in time: sometimes true (a promotion month, side work), often a typo.
    dated = [(j, _months(j.get("start"), today), _months(j.get("end"), today)) for j in shown]
    dated = [(j, s, e) for j, s, e in dated if s is not None and e is not None and e >= s]
    overlaps = []
    for i, (a, a_start, a_end) in enumerate(dated):
        for b, b_start, b_end in dated[i + 1:]:
            if min(a_end, b_end) - max(a_start, b_start) > OVERLAP_MONTHS:
                overlaps.append((a, b))
    for a, b in overlaps[:MAX_OVERLAPS]:
        out.append(_item("tip", "Experience", f"{names[id(a)]} ({_text(a.get('start'))} to {_text(a.get('end'))}) overlaps {names[id(b)]} ({_text(b.get('start'))} to {_text(b.get('end'))}). Fine if you really held both; otherwise check the dates.", _text(b.get("company")) or _text(b.get("title"))))
    if len(overlaps) > MAX_OVERLAPS:
        out.append(_item("tip", "Experience", f"{len(overlaps) - MAX_OVERLAPS} more pairs of jobs overlap in time.", "experience:"))

    # Engagements are private notes under a job. A confidential client must never be named in anything that prints (fix); a client that
    # is saved but appears in no printed bullet is only advice, since engagements are mostly there for tailoring.
    printed_text = " ".join(printed).lower()
    for job in shown:
        unused = []
        for engagement in (job.get("engagements") or []):
            if not isinstance(engagement, dict) or not _text(engagement.get("client")):
                continue
            client = _text(engagement.get("client"))
            mentioned = client.lower() in printed_text
            if engagement.get("confidential") is True and mentioned:
                out.append(_item("fix", names[id(job)], f"{client} is marked confidential, but its name is printed in this resume. Take it out of the summary or bullets.", client))
            elif engagement.get("confidential") is not True and not mentioned:
                unused.append(client)
        if unused:
            listed = ", ".join(unused[:5]) + (f" and {len(unused) - 5} more" if len(unused) > 5 else "")
            out.append(_item("tip", names[id(job)], f"{listed} {'is' if len(unused) == 1 else 'are'} saved as engagements for this job but not mentioned in any printed bullet. Engagements mostly help tailor the resume to a job; add a bullet if you want a client to show on the page.", unused[0]))

    groups = [g for g in (data.get("skills") or []) if isinstance(g, dict)]
    items = [_text(i) for g in groups for i in (g.get("items") or []) if _text(i)]
    for group in groups:
        count = len([i for i in (group.get("items") or []) if _text(i)])
        label = _text(group.get("category") or group.get("name"))
        if count > SKILL_GROUP_ITEMS:
            out.append(_item("tip", f"Skills: {label or 'a group'}", f"This group lists {count} skills. A long list reads as padding; keep the ones you would want to be asked about.", label or "skills:"))
    if len(items) > SKILLS_TOTAL:
        out.append(_item("tip", "Skills", f"{len(items)} skills in all. Past about {SKILLS_TOTAL} the list stops saying what you are best at.", "skills:"))
    lowered = [i.lower() for i in items]
    repeats = sorted({i for i in lowered if lowered.count(i) > 1})
    if repeats:
        out.append(_item("tip", "Skills", f"Listed more than once: {', '.join(repeats[:6])}.", repeats[0]))

    out.sort(key=lambda s: s["level"] != "fix")  # stable: fixes first, each group in resume order
    return out[:MAX_SUGGESTIONS]
