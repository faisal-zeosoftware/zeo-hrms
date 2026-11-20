"""v1.79.1 – the PDF of a long report (more than 800 rows: a daily attendance report of 450 employees for three months is 41,584
rows, about 1,000 pages) is made in the background (DataTools/import_jobs.py):

* the table is cut in parts of 400 rows – each part is laid out on its own (a PDF may take at most 30 s; one PDF of 1,800 rows
  took longer, so such reports could not be printed at all);
* the parts are joined into one file and "Page x of y" is put on every page afterwards (one layout of the page numbers only,
  in the report's language, laid over the pages) – the numbering runs through the whole report;
* the window shows "Rows 8,000 of 41,584 · 212 pages"; the file is kept two days and downloaded from the report (the notice
  opens it).
"""
import io
import os
import time

from DataTools.import_jobs import _dir, _put, task

PART = 400


def pdf_path(schema, token):
    return os.path.join(_dir(), f'{schema}-{token}.pdf')


@task('DashboardManagement.report_jobs:report_pdf')
def report_pdf(rows, params, ctx):
    from django.utils import translation
    from pypdf import PdfReader, PdfWriter
    from .report_pdf import render_table
    job = ctx.job
    with translation.override(params.get('lang') or 'en'):
        writer = PdfWriter()
        pages = 0
        n = len(rows)
        parts = max(1, (n + PART - 1) // PART)
        for k in range(parts):
            chunk = rows[k * PART:(k + 1) * PART]
            pdf = render_table(params, chunk, first=k == 0, last=k == parts - 1, numbers=False, seconds=300)
            for p in PdfReader(io.BytesIO(pdf)).pages:
                writer.add_page(p)
                pages += 1
            for _ in chunk:
                ctx.row(True)
            job['pages'] = pages
            job['beat'] = time.time()
            _put(job)
        _number(writer, pages, params)
        try:   # the parts each carried their fonts and letterhead images: kept once in the joined file
            writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
        except Exception:   # noqa: BLE001 – an older pypdf
            pass
        out = pdf_path(job['schema'], job['token'])
        with open(out, 'wb') as fh:
            writer.write(fh)
    job['pages'] = pages
    job['file'] = params.get('name') or 'report.pdf'
    return {'file': job['file'], 'pages': pages, 'rows': n, 'message': f'{params.get("title") or "Report"}: {pages:,} pages ({n:,} rows) – ready to download.'}


def _number(writer, pages, params):
    """'Page x of y' on every page: the page numbers alone are laid out once (same page size and place as a short report) and
    laid over the pages of the joined file"""
    from pypdf import PdfReader
    from weasyprint import HTML
    size = 'A4 landscape' if params.get('landscape') else 'A4'
    pg = params.get('pg') or 'Page " counter(page) " of " counter(pages) "'
    rtl = (params.get('lang') or 'en').startswith('ar')
    css = (f'@page {{ size: {size}; margin: 13mm 11mm 15mm 11mm; @bottom-right {{ content: "{pg}"; font: 7pt "Noto Naskh Arabic", "Cairo", "Inter", sans-serif; color: #6b7080; }} }}'
           ' div { page-break-after: always; height: 1px; } div:last-child { page-break-after: auto; }')
    html = f'<html dir="{"rtl" if rtl else "ltr"}"><head><meta charset="utf-8"><style>{css}</style></head><body>' + '<div></div>' * pages + '</body></html>'
    ov = PdfReader(io.BytesIO(HTML(string=html).write_pdf()))
    for i, page in enumerate(writer.pages):
        if i < len(ov.pages):
            page.merge_page(ov.pages[i])
