"""Feedback on a draft: instant rule-based suggestions (app/feedback.py, sent with every preview) and the AI review (its own route,
the person's own key, only what prints is sent)."""
import json
import re
from datetime import date

import pytest
import yaml

from app import feedback, limits
from tests.test_accounts import ADMIN, key, people, person, production  # noqa: F401 - fixtures
from tests.test_builder import KEY_A, Fake, GOOD_YAML, accepts, key_storage  # noqa: F401 - key_storage is an autouse fixture


def where(suggestions, level=None):
    return [(s["where"], s["text"]) for s in suggestions if level in (None, s["level"])]


def check(text):
    return feedback.check_resume(yaml.safe_load(text))


FULL_RESUME = """
basics:
  name: Bob Builder
  title: Engineer
  email: bob@example.com
  phone: "555.1234"
  summary: Builds things well.
experience:
  - company: Acme
    title: Dev
    start: 2020
    end: Present
    bullets: ["Cut build time 40%", "Led a team of 5", "Saved $2M", "Shipped 12 releases", "Trained 9 engineers"]
skills:
  - category: Languages
    items: [Python, Go]
"""


def test_a_complete_resume_gets_no_suggestions():
    assert check(FULL_RESUME) == []


def test_missing_contact_details_and_summary_are_flagged():
    got = check("basics:\n  name: Bob\n")
    assert ("Contact", "There is no email address. A reader who likes the resume needs a way to reach you.") in where(got, "fix")
    texts = " ".join(t for _, t in where(got, "tip"))
    assert "phone" in texts and "headline title" in texts and "no summary" in texts


def test_a_long_summary_is_flagged_with_its_word_count():
    long_summary = " ".join(["word"] * 150)
    got = check(FULL_RESUME.replace("Builds things well.", long_summary))
    assert any("150 words" in t for _, t in where(got, "tip"))
    assert not any("Summary" == w for w, _ in where(check(FULL_RESUME)))  # a short one is fine


def test_a_job_with_missing_dates_title_or_company_is_a_fix_that_names_the_job():
    got = check("basics: {name: B, email: e, phone: p, title: t, summary: s}\nexperience:\n  - company: Acme\n    title: Dev\n    bullets: [x]\n  - bullets: [y]\n")
    fixes = where(got, "fix")
    assert ("Dev at Acme", "This job has no start date.") in fixes
    assert any(w == "Dev at Acme" and "no end date" in t and "Present" in t for w, t in fixes)
    assert any(w == "Job 2" and "no title" in t for w, t in fixes) and any(w == "Job 2" and "no company" in t for w, t in fixes)


def test_a_number_in_the_dates_does_not_count_as_missing():
    got = check(FULL_RESUME.replace("start: 2020", "start: 2020").replace("end: Present", "end: 2024"))
    assert not where(got, "fix")  # YAML reads 2020 as an int; it is still a date


def test_jobs_left_off_the_resume_are_not_checked():
    text = FULL_RESUME.replace("skills:", "  - company: Side\n    title: Contractor\n    include_in_resume: false\nskills:")
    assert check(text) == []


def test_no_bullets_too_many_bullets_and_long_or_repeated_bullets_are_flagged():
    many = ", ".join(f"'Did thing {n} for 3 teams'" for n in range(10))
    text = FULL_RESUME.replace('bullets: ["Cut build time 40%", "Led a team of 5", "Saved $2M", "Shipped 12 releases", "Trained 9 engineers"]', f"bullets: [{many}, 'Did thing 1 for 3 teams', '{'x' * 400}']")
    tips = " ".join(t for _, t in where(check(text), "tip"))
    assert "12 bullets" in tips and "401" not in tips and "400 characters long" in tips
    assert any("appears twice" in t for _, t in where(check(text), "fix"))
    bare = FULL_RESUME.replace('bullets: ["Cut build time 40%", "Led a team of 5", "Saved $2M", "Shipped 12 releases", "Trained 9 engineers"]', "bullets: []")
    assert any("no bullets" in t for _, t in where(check(bare), "tip"))


def test_resumes_with_almost_no_numbers_get_a_tip_but_a_short_one_does_not():
    plain = FULL_RESUME.replace('"Cut build time 40%", "Led a team of 5", "Saved $2M", "Shipped 12 releases", "Trained 9 engineers"', "'Led the team', 'Built the thing', 'Ran releases', 'Mentored people', 'Wrote docs'")
    assert any("contain a number" in t for _, t in where(check(plain), "tip"))
    few = plain.replace("'Mentored people', 'Wrote docs'", "")
    assert not any("contain a number" in t for _, t in where(check(few)))  # fewer than five bullets: too little to say


def test_several_present_jobs_a_padded_skills_list_and_repeated_skills_are_tips():
    two_present = FULL_RESUME.replace("skills:", "  - company: Other\n    title: Advisor\n    start: 2021\n    end: Present\n    bullets: ['Advised 3 firms']\nskills:")
    assert any("2 jobs are marked Present" in t for _, t in where(check(two_present), "tip"))
    padded = FULL_RESUME.replace("items: [Python, Go]", "items: [" + ", ".join(f"S{n}" for n in range(45)) + ", Python, Python]")
    tips = " ".join(t for _, t in where(check(padded), "tip"))
    assert "lists 47 skills" in tips and "47 skills in all" in tips and "more than once: python" in tips


def test_fixes_come_before_tips_and_the_list_is_capped():
    got = check("basics:\n  name: Bob\nexperience:\n  - bullets: [a]\n")
    levels = [s["level"] for s in got]
    assert levels == sorted(levels, key=lambda lv: lv != "fix")
    jobs = "\n".join(f"  - bullets: [a]" for _ in range(40))
    assert len(check(f"basics:\n  name: Bob\nexperience:\n{jobs}\n")) == feedback.MAX_SUGGESTIONS


def test_the_preview_carries_the_suggestions(person, people):
    bob = person("bob@example.com")
    got = bob.post("/api/builder/preview", json={"resume_yaml": GOOD_YAML}).json()
    assert any(s["where"] == "Contact" for s in got["suggestions"])  # GOOD_YAML has no email
    assert "suggestions" not in bob.post("/api/builder/preview", json={"resume_yaml": "a: [oops"}).json()


def tip_texts(text, today=date(2026, 11, 19)):
    return [t for _, t in where(feedback.check_resume(yaml.safe_load(text), today), "tip")]


def jobs_yaml(*jobs):
    """A complete resume around the given jobs, each (company, start, end, bullets-list-text, extra)."""
    body = "".join(
        f"  - company: {c}\n    title: Dev\n    start: {s}\n    end: {e}\n    bullets: {b}\n{extra}" for c, s, e, b, extra in jobs
    )
    return "basics: {name: B, title: T, email: e@x.com, phone: '1', summary: s}\nexperience:\n" + body


def test_a_past_job_written_in_present_tense_is_a_tip_but_a_current_job_is_not():
    past = jobs_yaml(("Acme", "Mar 2019", "Sep 2022", "['Lead a team of 5', 'Owns the roadmap for 3 products', 'Built a thing for 2 clients']", ""))
    got = [t for t in tip_texts(past) if "present tense" in t]
    assert got and "2 of its bullets are in present tense" in got[0] and "Lead a team" in got[0]
    one = jobs_yaml(("Acme", "Mar 2019", "Sep 2022", "['Lead a team of 5', 'Built a thing for 2 clients']", ""))
    assert any("1 of its bullets is in present tense" in t for t in tip_texts(one))
    now = jobs_yaml(("Acme", "Mar 2019", "Present", "['Lead a team of 5', 'Own the roadmap for 3 products']", ""))
    assert not [t for t in tip_texts(now) if "present tense" in t]
    fine = jobs_yaml(("Acme", "Mar 2019", "Sep 2022", "['Led a team of 5', 'Owned the roadmap for 3 products', 'Ran 12 releases', 'Set the 2021 plan']", ""))
    assert not [t for t in tip_texts(fine) if "present tense" in t]  # past tense, and verbs spelled the same in both are never flagged


def test_a_client_label_in_front_of_a_bullet_does_not_hide_its_tense():
    labelled = jobs_yaml(("Acme", "Mar 2019", "Sep 2022", "['Bungie: Lead the store build for 3 regions', 'Trex: Built 4 components']", ""))
    got = [t for t in tip_texts(labelled) if "present tense" in t]
    assert got and "1 of its bullets is in present tense" in got[0]  # "Lead" after "Bungie:" is caught, "Built" is not
    plain = jobs_yaml(("Acme", "Mar 2019", "Sep 2022", "['Bungie: Led the store build for 3 regions']", ""))
    assert not [t for t in tip_texts(plain) if "present tense" in t]


def test_overlapping_jobs_are_a_tip_but_a_handover_month_and_odd_date_formats_are_fine():
    overlap = jobs_yaml(("Acme", "Mar 2019", "Sep 2022", "['Led 3 teams']", ""), ("Globex", "June 2021", "2024", "['Led 4 teams']", ""))
    got = [t for t in tip_texts(overlap) if "overlaps" in t]
    assert len(got) == 1 and "Acme" in got[0] and "Globex" in got[0]
    handover = jobs_yaml(("Acme", "Mar 2019", "Sep 2022", "['Led 3 teams']", ""), ("Globex", "Sep 2022", "Present", "['Led 4 teams']", ""))
    assert not [t for t in tip_texts(handover) if "overlap" in t]
    odd = jobs_yaml(("Acme", "someday", "Sep 2022", "['Led 3 teams']", ""), ("Globex", "2021", "2024", "['Led 4 teams']", ""))
    assert not [t for t in tip_texts(odd) if "overlap" in t]  # a date it cannot read is skipped, never guessed


def overlaps(first_end, second_start, first_start="2020", second_end="Present"):
    return [t for t in tip_texts(jobs_yaml(("Acme", first_start, first_end, "['Led 3 teams']", ""), ("Globex", second_start, second_end, "['Led 4 teams']", ""))) if "overlaps" in t]


def test_jobs_overlap_only_when_the_first_ends_a_month_or_more_after_the_second_starts():
    assert not overlaps("May 2026", "May 2026")  # ended and started in the same month: a handover, not two jobs at once
    assert not overlaps("Apr 2026", "May 2026")
    assert overlaps("Jun 2026", "May 2026")  # ended a month after the next began: held both for sure
    assert overlaps("Dec 2026", "May 2026")


def test_a_bare_year_never_makes_an_overlap_that_the_months_could_not_have_been():
    assert not overlaps("2022", "2022")  # could be Mar to Sep: no way to know they overlapped
    assert not overlaps("2022", "2023")
    assert overlaps("2023", "2022")  # ends in 2023 in January at the earliest, starts in 2022 in December at the latest: overlap for sure


def test_the_overlap_tip_says_it_is_probably_a_wrong_date_and_points_at_the_exit_for_side_work():
    got = overlaps("Dec 2026", "May 2026")[0]
    assert "wrong date" in got and "include_in_resume: false" in got


def test_a_tip_about_the_second_job_at_one_company_points_at_the_second_one():
    text = jobs_yaml(("Rightpoint", "Mar 2019", "Present", "['Led 3 teams']", ""), ("Rightpoint", "Jan 2016", "Jul 2019", "['Built 4 sites']", "")).replace("    end: Present", "    end:")
    got = [s for s in check(text) if "no end date" in s["text"]]
    assert len(got) == 1 and got[0]["nth"] == 0  # the first job is the one with no end
    swapped = jobs_yaml(("Rightpoint", "Jan 2016", "Jul 2019", "['Built 4 sites']", ""), ("Rightpoint", "Mar 2019", "", "['Led 3 teams']", ""))
    second = [s for s in check(swapped) if "no end date" in s["text"]]
    assert len(second) == 1 and second[0]["nth"] == 1 and second[0]["find"][0] == "company: Rightpoint"
    overlap = [s for s in feedback.check_resume(yaml.safe_load(jobs_yaml(("Rightpoint", "Mar 2019", "Present", "['Led 3 teams']", ""), ("Rightpoint", "Jan 2016", "Jul 2019", "['Built 4 sites']", ""))), date(2026, 11, 19)) if "overlaps" in s["text"]]
    assert len(overlap) == 1 and overlap[0]["nth"] == 1  # the tip is about the later job in the file


def test_a_job_that_ends_before_it_starts_is_a_fix():
    got = where(check(jobs_yaml(("Acme", "Mar 2022", "Sep 2019", "['Led 3 teams']", ""))), "fix")
    assert any("before it starts" in t for _, t in got)
    assert not any("before it starts" in t for _, t in where(check(jobs_yaml(("Acme", "2022", "2022", "['Led 3 teams']", ""))), "fix"))  # a bare year: no way to know


ENGAGEMENTS = "    engagements:\n      - client: Bungie\n      - client: Nikon\n        confidential: true\n"


def test_saved_engagements_that_no_bullet_mentions_are_a_tip_naming_them():
    got = [t for t in tip_texts(jobs_yaml(("Acme", "2020", "Present", "['Led 3 teams']", ENGAGEMENTS))) if "engagements" in t]
    assert len(got) == 1 and got[0].startswith("Bungie is saved") and "Nikon" not in got[0]  # a confidential client is never suggested for a bullet
    printed = jobs_yaml(("Acme", "2020", "Present", "['Led 3 teams, including the Bungie site']", ENGAGEMENTS))
    assert not [t for t in tip_texts(printed) if "engagements" in t]


def test_a_confidential_client_named_in_a_printed_bullet_or_the_summary_is_a_fix():
    leaked = jobs_yaml(("Acme", "2020", "Present", "['Built the Nikon store for 3 regions']", ENGAGEMENTS))
    assert any("Nikon is marked confidential" in t for _, t in where(check(leaked), "fix"))
    in_summary = leaked.replace("summary: s", "summary: Worked with Nikon").replace("Built the Nikon store", "Built a store")
    assert any("Nikon is marked confidential" in t for _, t in where(check(in_summary), "fix"))
    assert not [t for _, t in where(check(jobs_yaml(("Acme", "2020", "Present", "['Led 3 teams']", ENGAGEMENTS))), "fix")]


def test_every_suggestion_lists_where_to_look_most_exact_first_and_ends_at_something_broad():
    got = check("basics:\n  name: Bob\nexperience:\n  - company: Acme\n    title: Dev\n    bullets: ['Same thing here', 'Same thing here']\n")
    dup = next(s for s in got if "appears twice" in s["text"])
    assert dup["find"][0] == "Same thing here" and "company: Acme" in dup["find"] and dup["find"][-1] == "experience:"
    assert next(s for s in got if "no email" in s["text"])["find"] == ["email:", "basics:"]


MESSY = """
basics: {name: B}
experience:
  - bullets: ['Lead a thing', 'Lead a thing']
  - company: Acme
    title: Dev
    start: Mar 2022
    end: Sep 2019
    bullets: ['Lead a team', 'Own the plan']
    engagements:
      - client: Bungie
      - client: Nikon
        confidential: true
  - company: Globex
    start: 2019
    end: Present
    bullets: []
  - company: Initech
    title: Dev
    start: 2020
    end: Present
    bullets: ['Built Nikon store']
skills:
  - category: Big
    items: [""" + ", ".join(f"S{n}" for n in range(50)) + """, S1]
"""


def test_no_suggestion_can_dead_end_whatever_rule_raised_it():
    got = check(MESSY)
    assert len(got) >= 12, [s["text"][:40] for s in got]  # the messy resume trips a wide range of rules
    for s in got:
        assert s["find"] and re.fullmatch(r"[a-z_]+:", s["find"][-1]), s  # the last resort is always a section or key, which always exists


# --- the AI review ---------------------------------------------------------------------------------------------------------

WITH_PRIVATE = """
basics: {name: Bob Builder, title: Engineer, email: bob@example.com}
experience:
  - company: Acme
    title: Dev
    start: 2020
    end: Present
    bullets: [Built things]
    engagements:
      - client: SECRET-CLIENT
        outcomes: [SECRET-OUTCOME]
  - company: Side Gig
    title: Contractor
    include_in_resume: false
    bullets: [OFF-RESUME-BULLET]
"""

REVIEW = json.dumps([
    {"where": "Dev at Acme", "quote": "Built things", "issue": "A bullet describes the company.", "suggestion": "Say what you did."},
    {"where": "Summary", "issue": "Says three but names five.", "suggestion": ""},
    {"nonsense": True}, 5,
])


def with_key(who):
    who.put("/api/api-key", json={"key": KEY_A})
    return who


def test_the_ai_review_returns_cleaned_suggestions_and_sends_only_what_prints(person, people, monkeypatch):
    accepts(monkeypatch)
    fake = Fake(monkeypatch, [REVIEW])
    bob = with_key(person("bob@example.com"))
    resp = bob.post("/api/builder/review", json={"resume_yaml": WITH_PRIVATE})
    assert resp.status_code == 200, resp.text
    assert resp.json()["suggestions"] == [
        {"where": "Dev at Acme", "quote": "Built things", "issue": "A bullet describes the company.", "suggestion": "Say what you did."},
        {"where": "Summary", "quote": "", "issue": "Says three but names five.", "suggestion": ""},
    ]
    sent = fake.calls[0]["messages"][0]["content"]
    assert fake.keys[-1] == KEY_A and "Built things" in sent
    assert sent.startswith(f"Today's date is {date.today().isoformat()}.")  # without it the reviewer calls a recent date "in the future"
    assert "never call it future-dated" in fake.calls[0]["system"]
    assert "SECRET-CLIENT" not in sent and "SECRET-OUTCOME" not in sent and "OFF-RESUME-BULLET" not in sent and "Side Gig" not in sent


def test_a_fenced_or_empty_review_is_fine(person, people, monkeypatch):
    accepts(monkeypatch)
    Fake(monkeypatch, ["```json\n" + REVIEW + "\n```", "[]"])
    bob = with_key(person("bob@example.com"))
    assert len(bob.post("/api/builder/review", json={"resume_yaml": GOOD_YAML}).json()["suggestions"]) == 2
    assert bob.post("/api/builder/review", json={"resume_yaml": GOOD_YAML}).json() == {"suggestions": []}


@pytest.mark.parametrize("answer", ["Looks good to me!", '{"where": "x"}'])
def test_an_unreadable_review_is_a_plain_message(person, people, monkeypatch, answer):
    accepts(monkeypatch)
    Fake(monkeypatch, [answer])
    resp = with_key(person("bob@example.com")).post("/api/builder/review", json={"resume_yaml": GOOD_YAML})
    assert resp.status_code == 400 and "couldn't be read" in resp.json()["detail"]


def test_a_review_needs_a_key_and_valid_yaml_and_calls_nothing_otherwise(person, people, monkeypatch):
    accepts(monkeypatch)
    fake = Fake(monkeypatch, [REVIEW])
    bob = person("bob@example.com")
    assert bob.post("/api/builder/review", json={"resume_yaml": GOOD_YAML}).status_code == 400  # no key saved yet
    with_key(bob)
    assert bob.post("/api/builder/review", json={"resume_yaml": "a: [oops"}).status_code == 400
    assert bob.post("/api/builder/review", json={"resume_yaml": "other: 1"}).status_code == 400
    assert bob.post("/api/builder/review", json={"resume_yaml": "basics: {name: x}\n# " + "x" * 61_000}).status_code == 400
    assert fake.calls == []


def test_reviews_have_their_own_hourly_limit_apart_from_drafts(person, people, monkeypatch):
    accepts(monkeypatch)
    Fake(monkeypatch, ["[]"] * (limits.MAX_DRAFTS + 2))
    bob = with_key(person("bob@example.com"))
    for _ in range(limits.MAX_DRAFTS):
        assert bob.post("/api/builder/review", json={"resume_yaml": GOOD_YAML}).status_code == 200
    resp = bob.post("/api/builder/review", json={"resume_yaml": GOOD_YAML})
    assert resp.status_code == 429 and "review" in resp.json()["detail"]
    assert all(isinstance(k, tuple) for k in limits._recent)
