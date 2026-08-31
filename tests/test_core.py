import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from job_scout.exporter import create_workbook
from job_scout.sources import canonical_url, category_for, extract_links, is_relevant, is_strict_early_career, is_us_location, parse_pipe_rows, parse_posted


class SourceTests(unittest.TestCase):
    def test_age_and_date_parsing(self):
        today = date(2026, 8, 31)
        self.assertEqual(parse_posted("2d", today), "2026-08-29")
        self.assertEqual(parse_posted("Aug 05", today), "2026-08-05")
        self.assertEqual(parse_posted("2026-08-30", today), "2026-08-30")

    def test_url_canonicalization_keeps_job_id(self):
        url = "https://boards.example/jobs/123?gh_jid=123&utm_source=list&ref=home"
        self.assertEqual(canonical_url(url), "https://boards.example/jobs/123?gh_jid=123")

    def test_role_scope(self):
        self.assertTrue(is_relevant("Machine Learning Engineer - New Grad 2027"))
        self.assertTrue(is_relevant("Junior Data Scientist"))
        self.assertFalse(is_relevant("Senior Software Engineer"))
        self.assertFalse(is_relevant("Software Engineering Intern"))
        self.assertEqual(category_for("ML Engineer, Graduate"), "AI / ML")
        self.assertTrue(is_strict_early_career("Software Engineer I"))
        self.assertFalse(is_strict_early_career("Software Engineer II"))

    def test_us_location_filter(self):
        self.assertTrue(is_us_location("Ontario, CA"))
        self.assertTrue(is_us_location("Remote, U.S."))
        self.assertFalse(is_us_location("Toronto, Canada"))

    def test_pipe_table_parser(self):
        rows = parse_pipe_rows("""| Company | Role | Location | Apply | Posted |
|---|---|---|---|---|
| Acme | Software Engineer I | Phoenix, AZ | [apply](https://example.com/job/1) | 2026-08-31 |
""")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["_company"], "Acme")

    def test_nested_markdown_image_link(self):
        value = "[![View](assets/view.svg)](https://example.com/job/1)"
        self.assertIn("https://example.com/job/1", extract_links(value))


class ExportTests(unittest.TestCase):
    def test_workbook_has_required_tabs(self):
        job = {
            "company": "Acme", "title": "Software Engineer I", "category": "Software Development",
            "location": "Phoenix, AZ", "posted_date": "2026-08-31", "url": "https://example.com/job/1",
            "sources": ["Test"], "grad_2027": True, "visa_status": "Likely — history",
            "visa_evidence": "Test evidence", "status": "Not applied", "saved": False,
            "first_seen": "2026-08-31", "last_seen": "2026-08-31", "notes": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = create_workbook(Path(directory) / "jobs.xlsx", [job])
            with zipfile.ZipFile(path) as archive:
                workbook = archive.read("xl/workbook.xml").decode()
                self.assertIn("All Matches", workbook)
                self.assertIn("H1B Sponsorship", workbook)
                self.assertIn("My Applications", workbook)


if __name__ == "__main__":
    unittest.main()
