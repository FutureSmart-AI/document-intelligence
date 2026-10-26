"""The resume sample's years_of_experience ground truth follows the field's own definition.

The `resume` template defines years_of_experience as a calculation over the dated work
history (backend/utils/config/enrichment_config.py): earliest job to the latest end, an
open end meaning the reference date, breaks between jobs left out, overlapping jobs counted
once, internships not counted. These tests recompute every resume's value from its own
ground-truth work history with that rule, so a fixture value and the definition the model
is given cannot drift apart. The three cases investigated on 2026-10-25 are pinned by name:
the ground truth was right for all three, the extraction was wrong
(backend/samples/resumes/EXTRACTION-YEARS-2026-10-25.md).

unittest style so CI's ``python -m unittest discover`` runs it in the API image.
"""

import json
import unittest
from pathlib import Path

from utils.config.enrichment_config import DOMAIN_FIELD_TEMPLATES

ROOT = Path(__file__).resolve().parents[2]
# backend/ in a checkout; the API image copies backend/ to /app, where tests/ is mounted.
_FIXTURE = next(p for p in (ROOT / "backend/samples/resumes/ground_truth.json",
                            ROOT / "samples/resumes/ground_truth.json") if p.exists())
GROUND_TRUTH = json.loads(_FIXTURE.read_text(encoding="utf8"))
REFERENCE_YEAR = int(GROUND_TRUTH["meta"]["reference_date"][:4])


def years_from_history(work_experience: list[dict]) -> int:
    """Whole years covered by at least one job; a same-year internship covers none."""
    covered = set()
    for job in work_experience:
        covered.update(range(job["start_year"], job["end_year"] or REFERENCE_YEAR))
    return len(covered)


class ResumeYearsDefinitionTests(unittest.TestCase):
    def test_every_resume_matches_its_work_history(self):
        for r in GROUND_TRUTH["resumes"]:
            with self.subTest(r["id"]):
                f = r["fields"]
                self.assertEqual(years_from_history(f["work_experience"]), f["years_of_experience"])

    def test_the_three_investigated_resumes(self):
        by_id = {r["id"]: r for r in GROUND_TRUTH["resumes"]}
        # r03 2010 - Present, no stated total; r15 2023 - Present, no stated total;
        # r08 teaching 2015 - 2022 plus data roles 2023 - Present, a bootcamp year between.
        for rid, expected in (("r03", 16), ("r15", 3), ("r08", 10)):
            with self.subTest(rid):
                f = by_id[rid]["fields"]
                self.assertEqual(f["years_of_experience"], expected)
                self.assertEqual(years_from_history(f["work_experience"]), expected)

    def test_definition_states_the_rules_the_ground_truth_uses(self):
        desc = next(f["description"] for f in DOMAIN_FIELD_TEMPLATES["resume"]
                    if f["field_name"] == "years_of_experience")
        for rule in ("reference date", "earliest job", "internship", "break", "not the total"):
            with self.subTest(rule):
                self.assertIn(rule, desc)


if __name__ == "__main__":
    unittest.main()
