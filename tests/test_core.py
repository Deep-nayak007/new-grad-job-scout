import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from job_scout.exporter import create_workbook
from job_scout.sources import (
    canonical_url, category_for, extract_links, is_relevant, is_strict_early_career,
    is_us_location, merge_jobs, parse_deel_job_page, parse_pipe_rows, parse_posted, parse_radar_jobs,
)


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
        self.assertFalse(is_relevant("Sales & Trading Analyst/Associate"))
        self.assertEqual(category_for("ML Engineer, Graduate"), "AI / ML")
        self.assertTrue(is_strict_early_career("Software Engineer I"))
        self.assertFalse(is_strict_early_career("Software Engineer II"))

    def test_us_location_filter(self):
        self.assertTrue(is_us_location("Ontario, CA"))
        self.assertTrue(is_us_location("Remote, U.S."))
        self.assertTrue(is_us_location("SpiderRock - Chicago Office"))
        self.assertFalse(is_us_location("Toronto, Canada"))

    def test_linkedin_radar_parser(self):
        text = '''[{"company":"SpiderRock","title":"2027 New Graduate Software Engineer","locations":["Chicago, IL"],"url":"https://www.linkedin.com/jobs/view/4461340622","posted":"2026-09-02","source":"LinkedIn"}]'''
        jobs = parse_radar_jobs(text, {"name": "2027 SWE Radar", "include_sources": ["LinkedIn"]})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["sources"], ["LinkedIn via 2027 SWE Radar"])
        self.assertTrue(jobs[0]["grad_2027"])

    def test_deel_job_page_parser(self):
        text = '''<script type="application/ld+json">{"@type":"JobPosting","title":"2027 New Graduate Software Engineer","description":"New graduates graduating May 2027.","datePosted":"2026-09-02T20:47:51Z","employmentType":["FULL_TIME"],"hiringOrganization":{"name":"SpiderRock"},"jobLocation":[{"address":{"addressLocality":"Chicago","addressRegion":"IL"}}],"url":"https://jobs.deel.com/spiderrock/job-details/abc/overview"}</script>'''
        jobs = parse_deel_job_page(text, "https://jobs.deel.com/spiderrock/job-details/abc/overview", "SpiderRock")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["posted_date"], "2026-09-02")
        self.assertEqual(jobs[0]["sources"], ["Deel Direct"])

    def test_cross_platform_duplicate_prefers_employer_url(self):
        common = {
            "title": "2027 New Graduate Software Engineer", "posted_date": "2026-09-02",
            "age_text": "1 day ago", "salary": "", "source_detail": "", "category": "Software Development",
            "grad_2027": True, "visa_status": "Unknown", "visa_evidence": "",
        }
        linkedin = {**common, "id": "linkedin", "company": "SpiderRock", "location": "Chicago, IL", "url": "https://www.linkedin.com/jobs/view/4461340622", "sources": ["LinkedIn"]}
        deel = {**common, "id": "deel", "company": "SpiderRock Technology Solutions LLC", "location": "SpiderRock - Chicago Office", "url": "https://jobs.deel.com/spiderrock/job-details/abc/overview", "sources": ["Deel Direct"]}
        jobs = merge_jobs([linkedin, deel])
        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0]["url"].startswith("https://jobs.deel.com/"))
        self.assertEqual(jobs[0]["sources"], ["Deel Direct", "LinkedIn"])

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
