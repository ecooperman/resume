"""Plain, rule-based feedback on a resume.yaml draft: things any reader can see are off (a job with no dates, a summary that runs
on, thirty skills). No AI and no key involved, so it is instant, free, and the page can re-run it on every keystroke. Judgment
calls (a bullet that is really company marketing, a list that says three and names five) are the AI review's job
(generate.review_resume). Every suggestion is {level, where, text}: level "fix" is something that will print wrong or break, "tip"
is advice. Nothing here changes the draft."""
import re
from typing import List

SUMMARY_WORDS = 120  # past this a summary stops being read
BULLET_CHARS = 350  # a bullet this long is a paragraph
MAX_BULLETS = 8  # per job
SKILL_GROUP_ITEMS = 15
SKILLS_TOTAL = 40
MAX_SUGGESTIONS = 30


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def _item(level: str, where: str, text: str) -> dict:
    return {"level": level, "where": where, "text": text}


def check_resume(data: dict) -> List[dict]:
    """Suggestions for an already-parsed resume (a dict with a `basics` section). Most important first: fixes, then tips."""
    out: List[dict] = []
    basics = data.get("basics") if isinstance(data.get("basics"), dict) else {}

    if not _text(basics.get("email")):
        out.append(_item("fix", "Contact", "There is no email address. A reader who likes the resume needs a way to reach you."))
    if not _text(basics.get("phone")):
        out.append(_item("tip", "Contact", "There is no phone number. Many readers still expect one; leave it off only on purpose."))
    if not _text(basics.get("title")):
        out.append(_item("tip", "Header", "There is no headline title under your name (for example 'Senior Solutions Architect')."))

    summary = _text(basics.get("summary"))
    words = len(summary.split())
    if not summary:
        out.append(_item("tip", "Summary", "There is no summary. Two to four sentences saying who you are and what you are best at is what most readers read first."))
    elif words > SUMMARY_WORDS:
        out.append(_item("tip", "Summary", f"The summary is {words} words. Readers skim: two to four sentences (about 60 to 90 words) get read, a long paragraph usually does not."))

    jobs = [j for j in (data.get("experience") or []) if isinstance(j, dict)]
    shown = [j for j in jobs if j.get("include_in_resume", True)]
    present = [j for j in shown if _text(j.get("end")).lower() in ("present", "current", "now")]
    if len(present) > 1:
        names = ", ".join(_text(j.get("company")) or "(no company)" for j in present)
        out.append(_item("tip", "Experience", f"{len(present)} jobs are marked Present ({names}). That is right only if you really hold them all at once."))

    with_numbers = total_bullets = 0
    for number, job in enumerate(shown, 1):
        name = " at ".join(p for p in (_text(job.get("title")), _text(job.get("company"))) if p) or f"Job {number}"
        if not _text(job.get("title")):
            out.append(_item("fix", name, "This job has no title, so it will print with a blank title line."))
        if not _text(job.get("company")):
            out.append(_item("fix", name, "This job has no company."))
        if not _text(job.get("start")):
            out.append(_item("fix", name, "This job has no start date."))
        if not _text(job.get("end")):
            out.append(_item("fix", name, "This job has no end date, so it prints open-ended. If you still work there, write Present."))
        bullets = [_text(b) for b in (job.get("bullets") or []) if _text(b)]
        if not bullets:
            out.append(_item("tip", name, "This job has no bullets, so it prints as just a title and dates."))
        if len(bullets) > MAX_BULLETS:
            out.append(_item("tip", name, f"This job has {len(bullets)} bullets. Past about {MAX_BULLETS} readers stop reading; keep the strongest and drop the rest."))
        seen = set()
        for bullet in bullets:
            key = re.sub(r"\W+", " ", bullet.lower()).strip()
            if key in seen:
                out.append(_item("fix", name, f"This bullet appears twice: \"{bullet[:90]}\""))
            seen.add(key)
            if len(bullet) > BULLET_CHARS:
                out.append(_item("tip", name, f"A bullet is {len(bullet)} characters long, which is a paragraph. Split it or cut it to the result: \"{bullet[:70]}...\""))
            total_bullets += 1
            with_numbers += bool(re.search(r"\d", bullet))
    if total_bullets >= 5 and with_numbers / total_bullets < 0.25:
        out.append(_item("tip", "Experience", f"Only {with_numbers} of your {total_bullets} bullets contain a number. Readers trust results they can measure (a percentage, a count, money, a time saved); add one where you know it."))

    groups = [g for g in (data.get("skills") or []) if isinstance(g, dict)]
    items = [_text(i) for g in groups for i in (g.get("items") or []) if _text(i)]
    for group in groups:
        count = len([i for i in (group.get("items") or []) if _text(i)])
        if count > SKILL_GROUP_ITEMS:
            out.append(_item("tip", f"Skills: {_text(group.get('category') or group.get('name')) or 'a group'}", f"This group lists {count} skills. A long list reads as padding; keep the ones you would want to be asked about."))
    if len(items) > SKILLS_TOTAL:
        out.append(_item("tip", "Skills", f"{len(items)} skills in all. Past about {SKILLS_TOTAL} the list stops saying what you are best at."))
    lowered = [i.lower() for i in items]
    repeats = sorted({i for i in lowered if lowered.count(i) > 1})
    if repeats:
        out.append(_item("tip", "Skills", f"Listed more than once: {', '.join(repeats[:6])}."))

    out.sort(key=lambda s: s["level"] != "fix")  # stable: fixes first, each group in resume order
    return out[:MAX_SUGGESTIONS]
