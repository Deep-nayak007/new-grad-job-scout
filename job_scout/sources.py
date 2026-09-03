from __future__ import annotations

import hashlib
import html
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any


LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+(?:\)[^\s)]*)?)\)|href=[\"'](https?://[^\"']+)", re.I)
TAG_RE = re.compile(r"<[^>]+>")
TRACKING_KEYS = {"ref", "source", "src", "gh_src", "lever-source", "jr_id", "trk", "trackingid"}
NEGATIVE_VISA_MARKERS = ("🛂", "🇺🇸", "does not sponsor", "no sponsorship", "without sponsorship")
SENIOR_MARKERS = ("senior", "staff", "principal", "manager", "director", "lead ", "architect", "sr.", "sr ")
EARLY_MARKERS = (
    "new grad", "new graduate", "graduate", "entry level", "entry-level", "junior", "associate",
    "early career", "college grad", "university grad", "engineer i", "engineer 1", "developer i",
    "analyst i", "2027", "2026", "amts", "development program", "rotation program",
)
ROLE_MARKERS = (
    "software", "developer", "data", "machine learning", "artificial intelligence", " ai ", " ml ",
    "analytics", "business intelligence", "systems analyst", "technology analyst", "computer", "cloud", "devops", "platform", "backend", "front end",
    "frontend", "full stack", "fullstack", "infrastructure", "security engineer", "cyber", "firmware",
    "algorithm", "quant", "site reliability", "sre", "technology", "technical", "database",
)
NON_FULL_TIME = ("intern", "internship", "co-op", "co op", "coop", "apprentice")
FOREIGN_LOCATIONS = (
    "canada", "toronto", "vancouver", "montreal", "united kingdom", "london, uk",
    "india", "bengaluru", "bangalore", "hyderabad", "germany", "berlin", "france", "paris",
    "ireland", "dublin", "australia", "sydney", "melbourne", "singapore", "japan", "tokyo",
    "netherlands", "amsterdam", "poland", "spain", "sweden", "brazil", "mexico", "israel",
)
US_STATE_RE = re.compile(
    r"(?:,|\b)(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b",
    re.I,
)
US_CITY_MARKERS = (
    "atlanta", "austin", "baltimore", "boston", "charlotte", "chicago", "dallas", "denver",
    "detroit", "houston", "los angeles", "miami", "new york", "philadelphia", "phoenix",
    "pittsburgh", "portland", "raleigh", "san diego", "san francisco", "san jose", "seattle",
    "silicon valley", "washington dc", "washington, dc",
)
AGGREGATOR_HOSTS = ("applyguy.ai", "jobright.ai", "simplify.jobs", "app.zapply.jobs", "newgrad-jobs.com")


@dataclass
class FetchResult:
    jobs: list[dict[str, Any]]
    source_results: dict[str, dict[str, Any]]


def clean_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>|</br>", " • ", value, flags=re.I)
    value = re.sub(r"</?(?:details|summary|sub|strong|div|p)[^>]*>", " ", value, flags=re.I)
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = TAG_RE.sub(" ", value)
    value = html.unescape(value)
    value = value.replace("**", "").replace("__", "").replace("↳", "")
    return re.sub(r"\s+", " ", value).strip(" |\t\n")


def extract_links(value: str) -> list[str]:
    links = []
    for match in LINK_RE.finditer(value):
        url = match.group(2) or match.group(3)
        if url:
            links.append(html.unescape(url))
    # Image-style Markdown links nest one link inside another. Capture every
    # absolute target as a fallback, then preserve order while deduplicating.
    links.extend(html.unescape(url) for url in re.findall(r"\]\((https?://[^\s)]+)\)", value, flags=re.I))
    return list(dict.fromkeys(links))


def canonical_url(url: str) -> str:
    url = html.unescape(url.strip())
    parsed = urllib.parse.urlsplit(url)
    query = []
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower.startswith("utm_") or key_lower in TRACKING_KEYS:
            continue
        query.append((key, value))
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urllib.parse.urlencode(query), ""))


def make_id(url: str, company: str, title: str, location: str) -> str:
    identity = canonical_url(url) if url else "|".join((company.lower(), title.lower(), location.lower()))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def choose_apply_url(links: list[str]) -> str:
    if not links:
        return ""
    usable = [url for url in links if "i.imgur.com" not in url and "githubusercontent.com" not in url]
    if not usable:
        return ""
    employer = [
        url for url in usable
        if "github.com/" not in url and not any(host in urllib.parse.urlsplit(url).netloc.lower() for host in AGGREGATOR_HOSTS)
    ]
    preferred = [url for url in usable if "github.com/" not in url and "simplify.jobs/c/" not in url]
    return canonical_url((employer or preferred or usable)[-1])


def parse_posted(value: str, today: date | None = None) -> str | None:
    today = today or date.today()
    value = clean_text(value).lower()
    if not value:
        return None
    iso = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", value)
    if iso:
        try:
            return date.fromisoformat(iso.group(1)).isoformat()
        except ValueError:
            pass
    age = re.search(r"\b(\d+)\s*(d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes|w|wk|wks|week|weeks|mo|month|months)\b", value)
    if age:
        amount, unit = int(age.group(1)), age.group(2)
        if unit.startswith(("h", "m")) and not unit.startswith("mo"):
            days = 0
        elif unit.startswith("w"):
            days = amount * 7
        elif unit.startswith("mo"):
            days = amount * 30
        else:
            days = amount
        return (today - timedelta(days=days)).isoformat()
    if value in {"today", "new", "0d"}:
        return today.isoformat()
    for pattern in ("%b %d", "%B %d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            if "%Y" not in pattern and "%y" not in pattern:
                parsed = datetime.strptime(f"{value.title()} {today.year}", f"{pattern} %Y").date()
                # A posting date cannot be in the future. Repositories often keep
                # older rows without a year, so a future month/day belongs to the
                # previous calendar year.
                if parsed > today:
                    parsed = parsed.replace(year=today.year - 1)
            else:
                parsed = datetime.strptime(value.title(), pattern).date()
            return parsed.isoformat()
        except ValueError:
            continue
    return None


def category_for(title: str) -> str:
    title_lower = f" {title.lower()} "
    if any(token in title_lower for token in ("machine learning", "artificial intelligence", " ai ", " ml ", "nlp", "computer vision", "research scientist")):
        return "AI / ML"
    if any(token in title_lower for token in ("data scientist", "data analyst", "data engineer", "analytics", "business intelligence", "quant")):
        return "Data Science"
    return "Software Development"


def is_relevant(title: str) -> bool:
    value = f" {title.lower()} "
    if any(token in value for token in NON_FULL_TIME):
        return False
    if not any(token in value for token in ROLE_MARKERS):
        return False
    if any(token in value for token in SENIOR_MARKERS) and not any(token in value for token in EARLY_MARKERS):
        return False
    return True


def is_strict_early_career(title: str, description: str = "") -> bool:
    title_value = f" {title.lower()} "
    unambiguous_markers = tuple(
        marker for marker in EARLY_MARKERS
        if marker not in {"engineer i", "engineer 1", "developer i", "analyst i"}
    )
    if any(marker in title_value for marker in unambiguous_markers):
        return True
    if re.search(r"\b(?:software |data |machine learning |cloud |platform |firmware )?(?:engineer|developer|analyst)\s+(?:i|1)\b", title_value, re.I):
        return True
    description_value = description.lower()
    signals = (
        "class of 2027", "graduating in 2027", "graduation in 2027", "2027 start", "recent graduate",
        "new college graduate", "new university graduate", "no prior professional experience",
        "0-2 years", "0–2 years", "0 to 2 years", "less than 2 years of experience",
    )
    return any(signal in description_value for signal in signals)


def is_us_location(location: str) -> bool:
    value = location.lower().strip()
    if not value:
        return True
    if any(marker in value for marker in FOREIGN_LOCATIONS):
        return False
    return bool(
        US_STATE_RE.search(location)
        or any(marker in value for marker in ("united states", "u.s.", "usa", "multiple u.s", "remote"))
        or any(marker in value for marker in US_CITY_MARKERS)
        or value in {"us", "united states of america"}
    )


def looks_2027(title: str, eligibility: str, source: str) -> bool:
    combined = f"{title} {eligibility}".lower()
    return "2027" in combined or "college grad 2027" in combined or source in {"Keryx 2027", "SpeedyApply 2027", "V's 2027 New Grad"}


def company_key(company: str) -> str:
    value = re.sub(r"[^a-z0-9]+", " ", company.lower())
    for suffix in (" incorporated", " corporation", " technologies", " technology", " solutions", " inc", " llc", " ltd", " company"):
        value = value.replace(suffix, "")
    return re.sub(r"\s+", " ", value).strip()


class MarkdownHTMLTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[dict[str, Any]]]] = []
        self.table: list[list[dict[str, Any]]] | None = None
        self.row: list[dict[str, Any]] | None = None
        self.cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs):
        if tag == "table":
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = {"text": "", "links": [], "header": tag == "th"}
        elif tag == "a" and self.cell is not None:
            href = dict(attrs).get("href")
            if href:
                self.cell["links"].append(html.unescape(href))
        elif tag in {"br", "summary"} and self.cell is not None:
            self.cell["text"] += " • "

    def handle_data(self, data: str):
        if self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag: str):
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.cell["text"] = clean_text(self.cell["text"])
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            if self.row:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            if self.table:
                self.tables.append(self.table)
            self.table = None


def split_pipe(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_pipe_rows(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    rows: list[dict[str, Any]] = []
    index = 0
    while index < len(lines) - 1:
        line = lines[index].strip()
        next_line = lines[index + 1].strip()
        if line.startswith("|") and next_line.startswith("|") and re.match(r"^\|?[\s:|-]+\|?$", next_line):
            headers = [clean_text(cell).lower() for cell in split_pipe(line)]
            index += 2
            previous_company = ""
            while index < len(lines) and lines[index].strip().startswith("|"):
                raw_cells = split_pipe(lines[index])
                if len(raw_cells) >= len(headers):
                    cells = raw_cells[: len(headers) - 1] + [" | ".join(raw_cells[len(headers) - 1 :])]
                    row = {headers[i]: cells[i] if i < len(cells) else "" for i in range(len(headers))}
                    company_raw = row.get("company", "")
                    company = clean_text(company_raw)
                    if not company:
                        company = previous_company
                    else:
                        previous_company = company
                    row["_company"] = company
                    row["_raw"] = lines[index]
                    rows.append(row)
                index += 1
            continue
        index += 1
    return rows


def parse_html_rows(text: str) -> list[dict[str, Any]]:
    parser = MarkdownHTMLTableParser()
    parser.feed(text)
    result: list[dict[str, Any]] = []
    for table in parser.tables:
        if len(table) < 2:
            continue
        headers = [cell["text"].lower() for cell in table[0]]
        previous_company = ""
        for cells in table[1:]:
            if len(cells) < 4:
                continue
            row: dict[str, Any] = {}
            for idx, header in enumerate(headers):
                if idx < len(cells):
                    row[header] = cells[idx]["text"]
                    row[f"_{header}_links"] = cells[idx]["links"]
            company = clean_text(row.get("company", ""))
            if not company:
                company = previous_company
            else:
                previous_company = company
            row["_company"] = company
            row["_raw"] = " ".join(cell["text"] for cell in cells)
            result.append(row)
    return result


def first_value(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        if key in row:
            return str(row[key])
    return ""


def normalize_row(row: dict[str, Any], source: dict[str, Any]) -> dict[str, Any] | None:
    company = clean_text(row.get("_company", ""))
    title_raw = first_value(row, ("role", "position", "job title", "title"))
    title = clean_text(title_raw)
    location = clean_text(first_value(row, ("location", "locations")))
    posted_raw = first_value(row, ("posted", "date posted", "date", "age"))
    eligibility = clean_text(first_value(row, ("academic eligibility", "eligibility")))
    salary = clean_text(first_value(row, ("salary", "compensation")))
    source_detail = clean_text(first_value(row, ("seen in", "source", "sources")))
    level = clean_text(first_value(row, ("level",)))
    raw = str(row.get("_raw", ""))

    title_key = next((key for key in ("role", "position", "job title", "title") if key in row), "")
    apply_key = next((key for key in ("apply", "actions", "posting", "application/link", "application", "link") if key in row), "")
    links = list(row.get(f"_{apply_key}_links", [])) if apply_key else []
    if apply_key:
        links.extend(extract_links(str(row.get(apply_key, ""))))
    if not links and title_key:
        links.extend(row.get(f"_{title_key}_links", []))
        links.extend(extract_links(str(row.get(title_key, ""))))
    url = choose_apply_url(links)
    if not (company and title and url) or "🔒" in raw or "(m/w/d)" in title.lower() or not is_relevant(title) or not is_us_location(location):
        return None
    if source["kind"] == "markdown_strict" and not is_strict_early_career(title):
        return None

    visa_status = "Unknown"
    visa_evidence = ""
    combined_raw = f"{raw} {title_raw}".lower()
    if any(marker.lower() in combined_raw for marker in NEGATIVE_VISA_MARKERS):
        visa_status = "No / restricted"
        visa_evidence = "Listing is marked as not sponsoring or requiring U.S. work authorization."

    if source["kind"] == "h1b":
        if level and not any(marker in f" {title.lower()} {level.lower()} " for marker in EARLY_MARKERS):
            return None
        h1b = first_value(row, ("h1b status", "h1b", "visa"))
        if "🏅" in h1b or "🏅" in raw:
            visa_status = "Yes — explicit"
            visa_evidence = "Jobright reports that sponsorship is explicitly mentioned in the job description. Verify on the employer posting."
        else:
            visa_status = "Likely — history"
            visa_evidence = "Jobright reports recent sponsorship history for this employer/category. This is not a guarantee."
    visa_cell = clean_text(first_value(row, ("visa", "h1b status", "h1b")))
    if visa_status == "Unknown" and "h-1b co" in visa_cell.lower():
        visa_status = "Likely — history"
        visa_evidence = "The source marks this employer as an H-1B sponsor. Confirm sponsorship for this specific role."

    return {
        "id": make_id(url, company, title, location),
        "company": company,
        "title": title,
        "location": location,
        "url": url,
        "posted_date": parse_posted(posted_raw),
        "age_text": clean_text(posted_raw),
        "salary": salary,
        "sources": [source["name"]],
        "source_detail": source_detail,
        "category": category_for(title),
        "grad_2027": looks_2027(title, eligibility, source["name"]),
        "visa_status": visa_status,
        "visa_evidence": visa_evidence,
    }


def fetch_text(url: str, timeout: int = 35) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "JobScout/1.0 (+local personal job tracker)", "Accept": "text/plain,*/*"},
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        exc.close()
        raise


def fetch_json(url: str, timeout: int = 25) -> Any:
    return json.loads(fetch_text(url, timeout=timeout))


def parse_iso_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000).date().isoformat()
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return parse_posted(text)


def visa_from_description(description: str) -> tuple[str, str]:
    value = clean_text(description).lower()
    negative = (
        "unable to sponsor", "will not sponsor", "does not sponsor", "no visa sponsorship",
        "not eligible for visa sponsorship", "without current or future sponsorship",
        "without the need for sponsorship", "must be authorized to work in the united states without sponsorship",
    )
    positive = (
        "h-1b sponsorship is available", "h1b sponsorship is available", "we sponsor h-1b",
        "we sponsor h1b", "visa sponsorship is available", "eligible for visa sponsorship",
    )
    if any(phrase in value for phrase in negative):
        return "No / restricted", "The employer description says sponsorship is unavailable or requires independent U.S. work authorization."
    if any(phrase in value for phrase in positive):
        return "Yes — explicit", "The direct employer description explicitly mentions visa or H-1B sponsorship availability."
    return "Unknown", ""


def ats_job(
    *, company: str, title: str, location: str, url: str, published: Any,
    description: str, source_name: str,
) -> dict[str, Any] | None:
    if not (company and title and url) or not is_relevant(title) or not is_strict_early_career(title, description):
        return None
    if not is_us_location(location):
        return None
    visa_status, visa_evidence = visa_from_description(description)
    posted_date = parse_iso_date(published)
    return {
        "id": make_id(url, company, title, location),
        "company": clean_text(company),
        "title": clean_text(title),
        "location": clean_text(location),
        "url": canonical_url(url),
        "posted_date": posted_date,
        "age_text": posted_date or "",
        "salary": "",
        "sources": [source_name],
        "source_detail": "Direct public employer ATS feed",
        "category": category_for(title),
        "grad_2027": looks_2027(title, description, ""),
        "visa_status": visa_status,
        "visa_evidence": visa_evidence,
    }


def parse_radar_jobs(text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("Radar feed must contain a JSON list")
    included = {str(value).lower() for value in source.get("include_sources", [])}
    jobs = []
    for item in data:
        if not isinstance(item, dict):
            continue
        origin = clean_text(str(item.get("source", ""))) or source["name"]
        if included and origin.lower() not in included:
            continue
        locations = item.get("locations", "")
        location = " • ".join(clean_text(str(value)) for value in locations) if isinstance(locations, list) else clean_text(str(locations))
        source_name = f"{origin} via {source['name']}" if origin.lower() != source["name"].lower() else source["name"]
        candidate = ats_job(
            company=clean_text(str(item.get("company", ""))),
            title=clean_text(str(item.get("title", ""))),
            location=location,
            url=str(item.get("url", "")),
            published=item.get("posted"),
            description="",
            source_name=source_name,
        )
        if not candidate:
            continue
        candidate["source_detail"] = "Public early-career discovery feed"
        sponsorship = clean_text(str(item.get("sponsorship", ""))).lower()
        if "citizenship" in sponsorship or "does not sponsor" in sponsorship:
            candidate["visa_status"] = "No / restricted"
            candidate["visa_evidence"] = "The discovery feed marks this listing as requiring U.S. citizenship or not sponsoring. Verify with the employer."
        jobs.append(candidate)
    return jobs


def json_ld_objects(text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    pattern = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
    for match in pattern.finditer(text):
        try:
            value = json.loads(html.unescape(match.group(1)))
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = value if isinstance(value, list) else [value]
        objects.extend(item for item in candidates if isinstance(item, dict))
    return objects


def deel_location(posting: dict[str, Any]) -> str:
    locations = posting.get("jobLocation") or []
    if isinstance(locations, dict):
        locations = [locations]
    values = []
    for location in locations:
        address = location.get("address", {}) if isinstance(location, dict) else {}
        if isinstance(address, str):
            values.append(address)
            continue
        parts = [address.get(key) for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key)]
        if parts:
            values.append(", ".join(dict.fromkeys(map(str, parts))))
    return " • ".join(dict.fromkeys(values))


def parse_deel_job_page(text: str, url: str, fallback_company: str) -> list[dict[str, Any]]:
    jobs = []
    for posting in json_ld_objects(text):
        posting_type = posting.get("@type", "")
        posting_types = posting_type if isinstance(posting_type, list) else [posting_type]
        if "JobPosting" not in posting_types:
            continue
        employment = posting.get("employmentType") or []
        employment_text = " ".join(map(str, employment)) if isinstance(employment, list) else str(employment)
        if employment_text and "full" not in employment_text.lower():
            continue
        organization = posting.get("hiringOrganization") or {}
        company = organization.get("name", "") if isinstance(organization, dict) else ""
        candidate = ats_job(
            company=company or fallback_company,
            title=str(posting.get("title", "")),
            location=deel_location(posting),
            url=str(posting.get("url") or url),
            published=posting.get("datePosted"),
            description=str(posting.get("description", "")),
            source_name="Deel Direct",
        )
        if candidate:
            jobs.append(candidate)
    return jobs


def discover_ats_boards(jobs: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    boards: dict[str, dict[str, Counter[str]]] = {"greenhouse": {}, "lever": {}, "ashby": {}, "deel": {}}
    for job in jobs:
        parsed = urllib.parse.urlsplit(job["url"])
        parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
        kind = ""
        token = ""
        if parsed.netloc.lower() in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and parts:
            kind, token = "greenhouse", parts[0]
            if token in {"embed", "job_app"}:
                continue
        elif parsed.netloc.lower() == "jobs.lever.co" and parts:
            kind, token = "lever", parts[0]
        elif parsed.netloc.lower() == "jobs.ashbyhq.com" and parts:
            kind, token = "ashby", parts[0]
        elif parsed.netloc.lower() == "jobs.deel.com" and parts:
            kind, token = "deel", parts[0]
        if kind and token:
            boards[kind].setdefault(token, Counter())[job["company"]] += 1
    return {
        kind: {token: companies.most_common(1)[0][0] for token, companies in tokens.items()}
        for kind, tokens in boards.items()
    }


def fetch_greenhouse_board(token: str, fallback_company: str) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote(token, safe="")
    data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{encoded}/jobs?content=true")
    jobs = []
    for item in data.get("jobs", []):
        description = item.get("content", "")
        company = item.get("company_name") or fallback_company
        candidate = ats_job(
            company=company, title=item.get("title", ""), location=(item.get("location") or {}).get("name", ""),
            url=item.get("absolute_url", ""), published=item.get("first_published") or item.get("updated_at"),
            description=description, source_name="Greenhouse Direct",
        )
        if candidate:
            jobs.append(candidate)
    return jobs


def fetch_lever_board(token: str, fallback_company: str) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote(token, safe="")
    data = fetch_json(f"https://api.lever.co/v0/postings/{encoded}?mode=json")
    jobs = []
    for item in data if isinstance(data, list) else []:
        categories = item.get("categories") or {}
        commitment = str(categories.get("commitment", "")).lower()
        if commitment and "full" not in commitment:
            continue
        location = categories.get("location", "") or ", ".join(categories.get("allLocations") or [])
        description = " ".join(str(item.get(key, "")) for key in ("descriptionPlain", "additionalPlain", "openingPlain"))
        candidate = ats_job(
            company=fallback_company, title=item.get("text", ""), location=location,
            url=item.get("applyUrl") or item.get("hostedUrl", ""), published=item.get("createdAt"),
            description=description, source_name="Lever Direct",
        )
        if candidate:
            jobs.append(candidate)
    return jobs


def fetch_ashby_board(token: str, fallback_company: str) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote(token, safe="")
    data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{encoded}?includeCompensation=true")
    jobs = []
    for item in data.get("jobs", []):
        employment = str(item.get("employmentType", "")).lower()
        if employment and employment not in {"fulltime", "full-time", "full time"}:
            continue
        description = item.get("descriptionPlain") or item.get("descriptionHtml", "")
        candidate = ats_job(
            company=fallback_company, title=item.get("title", ""), location=item.get("location", ""),
            url=item.get("applyUrl") or item.get("jobUrl", ""), published=item.get("publishedAt"),
            description=description, source_name="Ashby Direct",
        )
        if candidate:
            jobs.append(candidate)
    return jobs


def fetch_deel_board(token: str, fallback_company: str) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote(token, safe="-")
    board_text = fetch_text(f"https://jobs.deel.com/{encoded}")
    objects = json_ld_objects(board_text)
    organization = next((item for item in objects if item.get("@type") == "Organization"), {})
    company = clean_text(str(organization.get("name", ""))) or fallback_company
    listing = next((item for item in objects if item.get("@type") == "ItemList"), {})
    urls = []
    for item in listing.get("itemListElement", []):
        if not isinstance(item, dict):
            continue
        nested = item.get("item") or {}
        url = item.get("url") or (nested.get("url") if isinstance(nested, dict) else "")
        if url:
            urls.append(str(url))
    jobs = []
    for url in dict.fromkeys(urls):
        jobs.extend(parse_deel_job_page(fetch_text(url), url, company))
    return jobs


def fetch_direct_ats(curated_jobs: list[dict[str, Any]], settings: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    boards = discover_ats_boards(curated_jobs)
    for seed in settings.get("deel_boards", []):
        token = clean_text(str(seed.get("slug", "")))
        if token:
            boards["deel"].setdefault(token, clean_text(str(seed.get("company", token))))
    max_boards = int(settings.get("max_boards_per_platform", 140))
    tasks = []
    for kind, tokens in boards.items():
        for token, company in list(tokens.items())[:max_boards]:
            tasks.append((kind, token, company))
    fetchers = {"greenhouse": fetch_greenhouse_board, "lever": fetch_lever_board, "ashby": fetch_ashby_board, "deel": fetch_deel_board}
    jobs: list[dict[str, Any]] = []
    summaries = {kind: {"status": "ok", "jobs": 0, "boards": 0, "errors": 0} for kind in fetchers}
    with ThreadPoolExecutor(max_workers=int(settings.get("max_workers", 12))) as executor:
        future_map = {
            executor.submit(fetchers[kind], token, company): kind for kind, token, company in tasks
        }
        for future in as_completed(future_map):
            kind = future_map[future]
            summaries[kind]["boards"] += 1
            try:
                found = future.result()
                jobs.extend(found)
                summaries[kind]["jobs"] += len(found)
            except Exception:
                summaries[kind]["errors"] += 1
    labels = {"greenhouse": "Greenhouse Direct", "lever": "Lever Direct", "ashby": "Ashby Direct", "deel": "Deel Direct"}
    results = {}
    for kind, summary in summaries.items():
        if summary["boards"] and summary["errors"] == summary["boards"]:
            summary["status"] = "error"
        results[labels[kind]] = summary
    return jobs, results


def listing_title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def listing_location_keys(location: str) -> set[str]:
    value = location.lower()
    keys = {f"city:{city}" for city in US_CITY_MARKERS if city in value}
    keys.update(f"state:{match.group(0).strip(', ').upper()}" for match in US_STATE_RE.finditer(location))
    if "remote" in value:
        keys.add("remote")
    return keys or {re.sub(r"[^a-z0-9]+", " ", value).strip()}


def same_listing(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if listing_title_key(left["title"]) != listing_title_key(right["title"]):
        return False
    left_company, right_company = company_key(left["company"]), company_key(right["company"])
    if not left_company or not right_company or not (left_company in right_company or right_company in left_company):
        return False
    return bool(listing_location_keys(left.get("location", "")) & listing_location_keys(right.get("location", "")))


def is_aggregator_url(url: str) -> bool:
    host = urllib.parse.urlsplit(url).netloc.lower()
    return "linkedin.com" in host or any(aggregator in host for aggregator in AGGREGATOR_HOSTS)


def merge_job_fields(existing: dict[str, Any], job: dict[str, Any], visa_rank: dict[str, int]) -> None:
    sources = sorted(set(existing["sources"] + job["sources"]))
    details = [value for value in (existing.get("source_detail"), job.get("source_detail")) if value]
    if is_aggregator_url(existing["url"]) and not is_aggregator_url(job["url"]):
        for key in ("company", "title", "location", "url", "salary", "category"):
            existing[key] = job.get(key, existing.get(key))
    existing["sources"] = sources
    existing["source_detail"] = " • ".join(dict.fromkeys(details))
    existing["grad_2027"] = existing["grad_2027"] or job["grad_2027"]
    if not existing.get("salary") and job.get("salary"):
        existing["salary"] = job["salary"]
    if not existing.get("posted_date") or (job.get("posted_date") and job["posted_date"] > existing["posted_date"]):
        existing["posted_date"] = job.get("posted_date")
        existing["age_text"] = job.get("age_text", "")
    if visa_rank.get(job["visa_status"], 0) > visa_rank.get(existing["visa_status"], 0):
        existing["visa_status"] = job["visa_status"]
        existing["visa_evidence"] = job["visa_evidence"]


def merge_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    by_title: dict[str, list[dict[str, Any]]] = {}
    visa_rank = {"Unknown": 0, "No / restricted": 1, "Likely — history": 2, "Yes — explicit": 3}
    for job in jobs:
        existing = by_id.get(job["id"])
        if not existing:
            existing = next((candidate for candidate in by_title.get(listing_title_key(job["title"]), []) if same_listing(candidate, job)), None)
        if not existing:
            merged.append(job)
            by_id[job["id"]] = job
            by_title.setdefault(listing_title_key(job["title"]), []).append(job)
            continue
        merge_job_fields(existing, job, visa_rank)
    for job in merged:
        job["id"] = make_id(job["url"], job["company"], job["title"], job["location"])
    return merged


def fetch_all(sources: list[dict[str, Any]], direct_ats: dict[str, Any] | None = None) -> FetchResult:
    all_jobs: list[dict[str, Any]] = []
    results: dict[str, dict[str, Any]] = {}
    sponsor_companies: dict[str, str] = {}

    ordered = sorted(sources, key=lambda item: 0 if item["kind"] == "h1b" else 1)
    for source in ordered:
        try:
            text = fetch_text(source["url"])
            if source["kind"] == "json_radar":
                parsed = parse_radar_jobs(text, source)
            else:
                rows = parse_pipe_rows(text)
                if source["kind"] == "markdown_html":
                    rows.extend(parse_html_rows(text))
                parsed = [job for row in rows if (job := normalize_row(row, source))]
            all_jobs.extend(parsed)
            if source["kind"] == "h1b":
                for job in parsed:
                    sponsor_companies[company_key(job["company"])] = job["visa_status"]
            results[source["name"]] = {"status": "ok", "jobs": len(parsed), "homepage": source.get("homepage", "")}
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            results[source["name"]] = {"status": "error", "jobs": 0, "error": str(exc)[:300], "homepage": source.get("homepage", "")}

    if direct_ats and direct_ats.get("enabled", True):
        ats_jobs, ats_results = fetch_direct_ats(all_jobs, direct_ats)
        all_jobs.extend(ats_jobs)
        results.update(ats_results)

    for job in all_jobs:
        if job["visa_status"] == "Unknown" and company_key(job["company"]) in sponsor_companies:
            job["visa_status"] = "Likely — history"
            job["visa_evidence"] = "The employer appears in Jobright's recent H-1B sponsorship-history feed. Confirm sponsorship for this specific role."
    return FetchResult(merge_jobs(all_jobs), results)
