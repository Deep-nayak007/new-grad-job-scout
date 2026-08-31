from __future__ import annotations

import html
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HEADERS = [
    "Company", "Role", "Category", "Location", "Date Posted", "Job URL", "Source(s)",
    "2027 Graduate Fit", "H1B Sponsorship", "Visa Evidence", "Application Status",
    "First Seen", "Last Seen", "Notes",
]


def column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def xml_text(value: Any) -> str:
    value = "" if value is None else str(value)
    cleaned = "".join(char for char in value if ord(char) in (9, 10, 13) or ord(char) >= 32)
    return html.escape(cleaned, quote=False)


def job_values(job: dict[str, Any]) -> list[str]:
    return [
        job["company"], job["title"], job["category"], job["location"], job.get("posted_date") or "",
        job["url"], ", ".join(job.get("sources", [])), "Yes" if job.get("grad_2027") else "General new grad",
        job["visa_status"], job.get("visa_evidence", ""), job.get("status", "Not applied"),
        job.get("first_seen", ""), job.get("last_seen", ""), job.get("notes", ""),
    ]


def worksheet_xml(jobs: list[dict[str, Any]]) -> tuple[str, str]:
    rows = [HEADERS] + [job_values(job) for job in jobs]
    row_xml = []
    hyperlinks = []
    relationships = []
    for row_number, values in enumerate(rows, start=1):
        cells = []
        for col_number, value in enumerate(values, start=1):
            reference = f"{column_name(col_number)}{row_number}"
            style = 1 if row_number == 1 else (3 if row_number % 2 == 0 else 2)
            cells.append(f'<c r="{reference}" s="{style}" t="inlineStr"><is><t>{xml_text(value)}</t></is></c>')
        row_xml.append(f'<row r="{row_number}" ht="{28 if row_number == 1 else 22}">{"".join(cells)}</row>')
        if row_number > 1 and len(values) >= 6 and values[5]:
            relation_id = f"rId{row_number - 1}"
            hyperlinks.append(f'<hyperlink ref="F{row_number}" r:id="{relation_id}"/>')
            relationships.append(
                f'<Relationship Id="{relation_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="{html.escape(str(values[5]), quote=True)}" TargetMode="External"/>'
            )
    last_row = max(1, len(rows))
    widths = [24, 48, 20, 34, 15, 46, 28, 19, 20, 54, 20, 24, 24, 45]
    cols = "".join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1))
    hyperlinks_xml = f'<hyperlinks>{"".join(hyperlinks)}</hyperlinks>' if hyperlinks else ""
    sheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
<cols>{cols}</cols><sheetData>{''.join(row_xml)}</sheetData>
<autoFilter ref="A1:N{last_row}"/>{hyperlinks_xml}
<pageMargins left="0.25" right="0.25" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>
</worksheet>'''
    rels = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{''.join(relationships)}</Relationships>'''
    return sheet, rels


def create_workbook(path: Path, all_jobs: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    visa_jobs = [job for job in all_jobs if job["visa_status"].startswith(("Yes", "Likely"))]
    my_jobs = [job for job in all_jobs if job.get("saved") or job.get("status") != "Not applied"]
    sheets = [("All Matches", all_jobs), ("H1B Sponsorship", visa_jobs), ("My Applications", my_jobs)]
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")

    content_types = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
                     '<Default Extension="xml" ContentType="application/xml"/>',
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
                     '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
                     '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>']
    for index in range(1, len(sheets) + 1):
        content_types.append(f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    content_types.append('</Types>')

    workbook_sheets = ''.join(f'<sheet name="{html.escape(name, quote=True)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1))
    workbook = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{workbook_sheets}</sheets><calcPr calcId="191029"/></workbook>'''
    workbook_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for index in range(1, len(sheets) + 1):
        workbook_rels.append(f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>')
    workbook_rels.append(f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')

    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Aptos"/><color rgb="FF172033"/></font><font><b/><sz val="11"/><name val="Aptos Display"/><color rgb="FFFFFFFF"/></font></fonts>
<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF173F5F"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF0F7F6"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border/><border><bottom style="thin"><color rgb="FFD6E1E0"/></bottom></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "".join(content_types))
        archive.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>''')
        archive.writestr("docProps/core.xml", f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>Job Scout - New Grad 2027</dc:title><dc:creator>Job Scout</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">{generated}</dcterms:created></cp:coreProperties>''')
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", "".join(workbook_rels))
        archive.writestr("xl/styles.xml", styles)
        for index, (_, jobs) in enumerate(sheets, 1):
            sheet, rels = worksheet_xml(jobs)
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet)
            archive.writestr(f"xl/worksheets/_rels/sheet{index}.xml.rels", rels)
    return path

