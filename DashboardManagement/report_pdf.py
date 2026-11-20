"""v1.69.0 – printed reports and the employee leave statement.

* ReportPdfView        POST/GET dashboard/api/reports/<key>/pdf/   – any report of the report centre (built-in or made with the report
                       builder, c-<id>) as a PDF on the company letterhead: title, period, branches, printed by / on, the table as the
                       screen shows it (filters, sort, columns, groups – the screen sends it like for the e-mail), totals, notes,
                       "Page x of y". A4, landscape for wide tables; right to left in Arabic.
* LeaveStatementView   GET dashboard/api/leave-statement/<emp>/?year=  (&pdf=1) – one employee's leave statement for a year:
                       per leave type the balance brought forward, earned, taken, returned, encashed, carried forward, lapsed,
                       adjusted and the closing balance, then every line with the balance after it. HR (the leave balance rights,
                       their branches) or the employee themselves (self service: emp = "me").
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import csv
import html
import io
import re
from collections import OrderedDict, defaultdict
from datetime import date, datetime

from django.apps import apps
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import current_lang, tr

M = apps.get_model
MAX_ROWS = 100000   # v1.79.1 (was 5,000): longer reports are made in the background in parts
SYNC_ROWS = 800     # v1.79.1 more rows than this: the background job (one PDF of 1,800 rows took more than the 30 s a PDF may take)
E = html.escape


def _lang():
    return 'ar' if str(current_lang() or 'en').startswith('ar') else 'en'


def _dmy(v):
    if isinstance(v, datetime):
        v = timezone.localtime(v) if timezone.is_aware(v) else v
        return v.strftime('%d/%m/%Y %H:%M')
    if isinstance(v, date):
        return v.strftime('%d/%m/%Y')
    if isinstance(v, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', v):
        return f'{v[8:10]}/{v[5:7]}/{v[:4]}'
    return v


def _num(v):
    if isinstance(v, bool) or v is None or v == '':
        return '' if v in (None, '') else (tr('Yes') if v else tr('No'))
    if isinstance(v, float):
        return f'{v:,.2f}'
    if isinstance(v, int):
        return f'{v:,}'
    return str(v)


def _is_num(s):
    return bool(re.match(r'^-?[\d,]+(\.\d+)?$', str(s or '').strip()))


def _page(title, head_html, body_html, landscape=False, small=False, letterhead=True, page_label=None, seconds=None):
    """the letterhead page (WeasyPrint). v1.79.1: letterhead=False for the later parts of a long report; page_label '' = no page numbers."""
    from PrintTemplates.contexts import branding, company_ctx
    from PrintTemplates.engine import family_stack, font_faces
    lang = _lang()
    b = branding()
    co = company_ctx(None, lang, b)
    fams = [b.font or 'Inter', b.font_arabic or 'Cairo', 'Noto Naskh Arabic']
    stack = ', '.join(family_stack(f) for f in fams) + ', sans-serif'
    name = (co.get('name_ar') or co['name']) if lang == 'ar' else co['name']
    logo = f'<img class="logo" src="{co["logo"]}" alt="">' if co.get('logo') else ''
    sub = ' · '.join(x for x in [co.get('address') or '', co.get('contact_line') or '', (f'TRN {co["trn"]}' if co.get('trn') else '')] if x)
    foot = E(co.get('footer_text') or '')
    size = 'A4 landscape' if landscape else 'A4'
    fs = {0: '8.4pt', 1: '7.4pt', 2: '6.2pt'}[int(small)]
    pad = '2px 2.5px' if int(small) == 2 else '3px 5px'
    pg = tr('Page {0} of {1}', '" counter(page) "', '" counter(pages) "') if page_label is None else page_label
    css = f"""{font_faces(fams)}
@page {{ size: {size}; margin: 13mm 11mm 15mm 11mm;
  @bottom-left {{ content: "{E(foot)[:160]}"; font: 7pt {stack}; color: #6b7080; }}
  @bottom-right {{ content: "{pg}"; font: 7pt {stack}; color: #6b7080; }} }}
* {{ box-sizing: border-box; }}
body {{ font-family: {stack}; color: {b.text_color or '#1f2430'}; font-size: {fs}; margin: 0; }}
.lh {{ display: flex; align-items: center; gap: 10px; border-bottom: 2px solid {b.primary_color or '#1f3a5f'}; padding-bottom: 6px; margin-bottom: 8px; }}
.lh .logo {{ max-height: 15mm; max-width: 45mm; }}
.lh .co {{ font-size: 12pt; font-weight: 700; color: {b.primary_color or '#1f3a5f'}; }}
.lh .sub {{ font-size: 7pt; color: #6b7080; }}
h1 {{ font-size: 13pt; margin: 2px 0 2px; color: {b.primary_color or '#1f3a5f'}; }}
.meta {{ color: #4b5060; margin-bottom: 8px; line-height: 1.5; }}
.meta b {{ color: #1f2430; font-weight: 600; }}
table {{ width: 100%; border-collapse: collapse; }}
thead {{ display: table-header-group; }}
th {{ background: #eef0f6; text-align: start; font-weight: 700; padding: {pad}; border-bottom: 1px solid #c9cdd9; vertical-align: bottom; }}
td {{ padding: {pad}; border-bottom: 0.6px solid #e3e5ec; vertical-align: top; }}
th, td {{ overflow-wrap: break-word; }}
td.w {{ overflow-wrap: anywhere; }}
.n span, span.l {{ direction: ltr; unicode-bidi: embed; }}
tr {{ page-break-inside: avoid; }}
.n {{ text-align: end; white-space: nowrap; font-variant-numeric: tabular-nums; overflow-wrap: normal; }}
tr.grp td {{ background: #f6f4fb; font-weight: 700; }}
tr.tot td {{ border-top: 1.2px solid #1f2430; font-weight: 700; background: #fafafa; }}
.notes {{ margin-top: 8px; color: #4b5060; font-size: 7.4pt; }}
h2 {{ font-size: 10pt; margin: 12px 0 4px; color: {b.primary_color or '#1f3a5f'}; }}
.cards {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 8px; }}
.card {{ border: 1px solid #dfe2ea; border-radius: 4px; padding: 4px 8px; min-width: 30mm; }}
.card span {{ display: block; color: #6b7080; font-size: 7pt; }}
.card b {{ font-size: 10pt; }}
"""
    doc = f"""<!doctype html><html lang="{lang}" dir="{'rtl' if lang == 'ar' else 'ltr'}"><head><meta charset="utf-8"><title>{E(title)}</title>
<style>{css}</style></head><body>
{f'<div class="lh">{logo}<div><div class="co">{E(name)}</div><div class="sub">{E(sub)}</div></div></div>' if letterhead else ''}
{head_html}{body_html}</body></html>"""
    from PrintTemplates.engine import html_to_pdf
    return html_to_pdf(doc, seconds=seconds)


def _printed_by(request):
    u = request.user
    who = (u.get_full_name() if hasattr(u, 'get_full_name') else '') or getattr(u, 'username', '')
    return tr('Printed by {0} on {1}', who, timezone.localtime().strftime('%d/%m/%Y %H:%M'))


def _branches_text(request):
    picked = [int(x) for x in re.findall(r'\d+', request.query_params.get('branch', '') or '')]
    names = list(M('OrganisationManager', 'brnch_mstr').objects.filter(pk__in=picked).values_list('branch_name', flat=True)) if picked else []
    out = ', '.join(names) if names else tr('All branches I may see')
    visa = [int(x) for x in re.findall(r'\d+', request.query_params.get('visa_branch', '') or '')]   # v1.73.24
    if visa:
        vn = list(M('OrganisationManager', 'brnch_mstr').objects.filter(pk__in=visa).values_list('branch_name', flat=True))
        out += ' · ' + tr('Visa branch: {0}', ', '.join(vn))
    return out


_AR = re.compile('[\u0600-\u06ff\ufb50-\ufdff\ufe70-\ufeff]')


def _cell(v):
    """a text cell: Latin text, dates and codes kept left to right inside an Arabic page; a long word (e-mail, IBAN) may break"""
    t = str(v)
    cls = ' class="w"' if any(len(w) > 16 for w in t.split()) else ''
    return f'<td{cls}>{E(t)}</td>' if (not t or _AR.search(t)) else f'<td{cls}><span class="l">{E(t)}</span></td>'


def _pdf_response(pdf, name, inline=True):
    r = HttpResponse(pdf, content_type='application/pdf')
    r['Content-Disposition'] = f'{"inline" if inline else "attachment"}; filename="{name}"'
    return r


# ------------------------------------------------------------------ any report
class ReportPdfView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, key):
        return self.make(request, key, None)

    def post(self, request, key):
        shown = request.data.get('csv') if isinstance(request.data, dict) else None
        return self.make(request, key, shown if isinstance(shown, str) and shown.strip() and len(shown) <= 5 * 1024 * 1024 else None,
                         (request.data.get('filters') or '') if isinstance(request.data, dict) else '')

    def make(self, request, key, shown, filters_text=''):
        from .reports import _run_report
        spec, data = _run_report(request, key)   # the rights and the period checks of the report itself
        if spec is None:
            return data
        if key in ('gl-trial-balance', 'gl-profit-loss', 'gl-balance-sheet') and request.query_params.get('layout') == 't':
            # v1.79.3 T format printed side by side
            from ZeoLedger.statement_pdf import render_t
            try:
                pdf = render_t(request, key, data)
            except Exception as exc:   # noqa: BLE001
                return Response({'detail': tr('The PDF could not be made: {0}', str(exc)[:200])}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            return _pdf_response(pdf, f"{key}-t-{_zt_today().isoformat()}.pdf", inline=request.query_params.get('download') != '1')
        if key.startswith('gl-fr-'):   # v1.79.4 designed statements: indents, bold headings, ruled totals on the letterhead
            from ZeoLedger.statement_pdf import render_fr
            try:
                pdf = render_fr(request, key, data)
            except Exception as exc:   # noqa: BLE001
                return Response({'detail': tr('The PDF could not be made: {0}', str(exc)[:200])}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            return _pdf_response(pdf, f"{key}-{_zt_today().isoformat()}.pdf", inline=request.query_params.get('download') != '1')
        if key in ('gl-customer-statement', 'gl-supplier-statement', 'gl-customer-ledger', 'gl-supplier-ledger'):
            # v1.79.3 the customer / supplier statement and ledger print as documents: one per partner, letterhead with the logo
            from ZeoLedger.statement_pdf import render as party_pdf
            try:
                pdf = party_pdf(request, key, data, shown)
            except Exception as exc:   # noqa: BLE001 – WeasyPrint missing / failed
                return Response({'detail': tr('The PDF could not be made: {0}', str(exc)[:200])}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            if pdf is not None:
                return _pdf_response(pdf, f"{key}-{_zt_today().isoformat()}.pdf", inline=request.query_params.get('download') != '1')
        types = {c['label']: c.get('type') for c in data['columns']}
        if shown:   # the table as the screen shows it (filters, sort, visible columns, a column per group level)
            rows = list(csv.reader(io.StringIO(shown.lstrip('﻿'))))
            heads, body = (rows[0], rows[1:]) if rows else ([], [])
            numeric = [types.get(h) == 'number' or (types.get(h) is None and body and all(_is_num(r[i]) for r in body if i < len(r) and r[i] != ''))
                       and any(i < len(r) and r[i] != '' for r in body) for i, h in enumerate(heads)]
            cells = [[(r[i] if i < len(r) else '') for i in range(len(heads))] for r in body]
            # the screen sends raw numbers: show them with thousands separators
            for r in cells:
                for i, v in enumerate(r):
                    if numeric[i] and _is_num(v):
                        try:
                            f = float(v.replace(',', ''))
                            r[i] = f'{f:,.2f}' if (f != int(f) or '.' in v) else f'{int(f):,}'
                        except ValueError:
                            pass
                    elif re.match(r'^\d{4}-\d{2}-\d{2}$', v or ''):
                        r[i] = _dmy(v)
            tot = None
            sums = {i: 0.0 for i, h in enumerate(heads) if numeric[i] and data['columns'] and any(c['label'] == h and c['key'] in data['totals']
                                                                                                    for c in data['columns'])}
            if sums:
                for r in body:
                    for i in sums:
                        try:
                            sums[i] += float((r[i] if i < len(r) else '0').replace(',', '') or 0)
                        except ValueError:
                            pass
                tot = [f'{sums[i]:,.2f}' if i in sums else '' for i in range(len(heads))]
        else:
            cols = data['columns']
            heads = [c['label'] for c in cols]
            numeric = [c.get('type') == 'number' for c in cols]
            cells = [[('' if r.get(c['key']) is None else (_num(r.get(c['key'])) if c.get('type') == 'number' or isinstance(r.get(c['key']), bool)
                                                          else str(_dmy(r.get(c['key'])))))
                      for c in cols] for r in data['rows']]
            tot = [(f"{data['totals'][c['key']]:,.2f}" if c['key'] in data['totals'] else '') for c in cols] if data.get('totals') else None
        if len(cells) > MAX_ROWS:
            return Response({'detail': tr('The report has {0} rows – at most {1} can be printed. Narrow it with the period, branch or filters.',
                                          f'{len(cells):,}', f'{MAX_ROWS:,}')}, status=status.HTTP_400_BAD_REQUEST)
        if tot and not any(tot):
            tot = None
        if tot:
            tot[0] = tot[0] or tr('Total')
        title = data['title']
        per = data.get('period')
        meta = []
        if per:
            meta.append(f"<b>{E(tr('Period'))}:</b> {E(_dmy(str(per['from'])))} – {E(_dmy(str(per['to'])))}")
        elif data.get('as_of'):
            meta.append(f"<b>{E(tr('As of'))}:</b> {E(_dmy(str(data['as_of'])))}")
        meta.append(f"<b>{E(tr('Branches'))}:</b> {E(_branches_text(request))}")
        if filters_text:
            meta.append(f"<b>{E(tr('Filters'))}:</b> {E(str(filters_text)[:400])}")
        meta.append(f"<b>{E(tr('Rows'))}:</b> {len(cells):,}")
        meta.append(E(_printed_by(request)))
        head = f'<h1>{E(title)}</h1><div class="meta">{" &nbsp;·&nbsp; ".join(meta)}</div>'
        notes = [str(n) for n in (data.get('notes') or [])]
        name = f"{key}-{_zt_today().isoformat()}.pdf"
        spec = {'title': title, 'head': head, 'heads': heads, 'numeric': numeric, 'tot': tot, 'notes': notes, 'landscape': len(heads) > 6,
                'small': 2 if len(heads) > 14 else (1 if len(heads) > 9 else 0), 'lang': _lang(), 'name': name,
                'pg': tr('Page {0} of {1}', '" counter(page) "', '" counter(pages) "'), 'empty': tr('No rows for this period and branch.')}
        if len(cells) > SYNC_ROWS:   # v1.79.1 a long report: made in the background in parts, with the count
            from DataTools import import_jobs as J
            from . import report_jobs  # noqa: F401 – registers the task
            job = J.start_task(request, 'DashboardManagement.report_jobs:report_pdf', cells,
                               dict(spec, page=f'/main-sidebar/report-options/r/{key}'), label=title, kind='pdf')
            return Response({'job': J.public(job)}, status=status.HTTP_202_ACCEPTED)
        try:
            pdf = render_table(spec, cells, first=True, last=True)
        except Exception as exc:   # noqa: BLE001 – WeasyPrint missing / failed
            return Response({'detail': tr('The PDF could not be made: {0}', str(exc)[:200])}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return _pdf_response(pdf, name, inline=request.query_params.get('download') != '1')


def render_table(spec, cells, first=True, last=True, numbers=True, seconds=None):
    """v1.79.1 the report table as a PDF: the whole report, or one part of a long one (first: the heading; last: the totals and
    notes; numbers=False: no "Page x of y" – a long report gets its page numbers after the parts are joined)."""
    numeric = spec['numeric']
    th = ''.join(f'<th class="{"n" if numeric[i] else ""}">{E(h)}</th>' for i, h in enumerate(spec['heads']))
    trs = []
    for r in cells:
        trs.append('<tr>' + ''.join((f'<td class="n"><span>{E(str(v))}</span></td>' if numeric[i] else _cell(v)) for i, v in enumerate(r)) + '</tr>')
    tot = spec.get('tot')
    if last and tot:
        trs.append('<tr class="tot">' + ''.join((f'<td class="n"><span>{E(str(v))}</span></td>' if numeric[i] and i else f'<td>{E(str(v))}</td>') for i, v in enumerate(tot)) + '</tr>')
    if first and last and not cells:
        trs.append(f'<tr><td colspan="{max(len(spec["heads"]), 1)}">{E(spec.get("empty") or "")}</td></tr>')
    notes = ''.join(f'<div>{E(str(n))}</div>' for n in (spec.get('notes') or [])) if last else ''
    body = f'<table><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table>' + (f'<div class="notes">{notes}</div>' if notes else '')
    return _page(spec['title'], spec['head'] if first else '', body, landscape=spec['landscape'], small=spec['small'],
                 letterhead=first, page_label=spec.get('pg') if numbers else '', seconds=seconds)


# ------------------------------------------------------------------ the employee's leave statement
ORDER = ['opening', 'accrual', 'taken', 'cancelled', 'encashed', 'carry_forward', 'lapsed', 'reset', 'adjustment']


def leave_statement(emp, year):
    """{'employee', 'year', 'types': [{name, brought, kinds{kind: days}, closing, lines: [{date, entry, days, balance, note}]}]}"""
    from LeavePolicy.models import LeaveLedger
    first, last = date(year, 1, 1), date(year, 12, 31)
    names = dict(M('calendars', 'leave_type').objects.values_list('id', 'name'))
    kinds = dict(LeaveLedger.KINDS)
    out = OrderedDict()
    bal = defaultdict(float)
    for l in LeaveLedger.objects.filter(employee_id=emp.pk, date__lte=last).order_by('leave_type_id', 'date', 'id'):
        t = out.get(l.leave_type_id)
        if l.date < first:
            bal[l.leave_type_id] += l.days
            continue
        if t is None:
            t = out[l.leave_type_id] = {'leave_type_id': l.leave_type_id, 'name': names.get(l.leave_type_id, ''), 'brought': round(bal[l.leave_type_id], 2),
                                        'kinds': OrderedDict((k, 0.0) for k in ORDER), 'lines': []}
        bal[l.leave_type_id] += l.days
        t['kinds'][l.kind] = round(t['kinds'].get(l.kind, 0.0) + l.days, 2)
        t['lines'].append({'date': l.date.isoformat(), 'entry': tr(kinds.get(l.kind, l.kind)), 'kind': l.kind, 'days': round(l.days, 2),
                           'balance': round(bal[l.leave_type_id], 2), 'note': l.note or ''})
    # types with a balance but no movement in the year
    for tid, b in bal.items():
        if tid not in out and abs(b) > 1e-9:
            out[tid] = {'leave_type_id': tid, 'name': names.get(tid, ''), 'brought': round(b, 2), 'kinds': OrderedDict((k, 0.0) for k in ORDER), 'lines': []}
    types = []
    for t in out.values():
        t['closing'] = round(t['brought'] + sum(t['kinds'].values()), 2)
        t['kinds'] = [{'kind': k, 'label': tr(kinds[k]), 'days': v} for k, v in t['kinds'].items() if v]
        types.append(t)
    types.sort(key=lambda x: x['name'])
    from PrintTemplates.contexts import employee_ctx
    e = employee_ctx(emp, _lang())
    return {'employee': {'id': emp.pk, 'name': e.get('name', ''), 'code': emp.emp_code, 'department': e.get('department', ''),
                         'designation': e.get('designation', ''), 'branch': e.get('branch', ''),
                         'joined': emp.emp_joined_date.isoformat() if getattr(emp, 'emp_joined_date', None) else ''},
            'year': year, 'types': types}


def _statement_pdf(request, st):
    e = st['employee']
    title = tr('Leave statement {0}', st['year'])
    head = (f'<h1>{E(title)}</h1><div class="meta"><b>{E(e["name"])}</b> ({E(e["code"] or "")}) &nbsp;·&nbsp; {E(e["designation"])} &nbsp;·&nbsp; '
            f'{E(e["department"])} &nbsp;·&nbsp; {E(e["branch"])}' + (f' &nbsp;·&nbsp; {E(tr("Joined"))} {E(_dmy(e["joined"]))}' if e['joined'] else '')
            + f'<br>{E(_printed_by(request))}</div>')
    parts = []
    # the summary: one row per leave type
    kinds = [k for k in ORDER if any(any(x['kind'] == k for x in t['kinds']) for t in st['types'])]
    from LeavePolicy.models import LeaveLedger
    kl = dict(LeaveLedger.KINDS)
    th = f'<th>{E(tr("Leave type"))}</th><th class="n">{E(tr("Brought forward"))}</th>' + ''.join(f'<th class="n">{E(tr(kl[k]))}</th>' for k in kinds) \
        + f'<th class="n">{E(tr("Balance on 31/12"))}</th>'
    rows = []
    for t in st['types']:
        kd = {x['kind']: x['days'] for x in t['kinds']}
        rows.append(f'<tr><td>{E(tr(t["name"]))}</td><td class="n"><span>{t["brought"]:,.2f}</span></td>' + ''.join(f'<td class="n"><span>{kd.get(k, 0):,.2f}</span></td>' for k in kinds)
                    + f'<td class="n"><span><b>{t["closing"]:,.2f}</b></span></td></tr>')
    if not rows:
        rows.append(f'<tr><td colspan="{len(kinds) + 3}">{E(tr("No leave balance or movement in this year."))}</td></tr>')
    parts.append(f'<h2>{E(tr("Summary (days)"))}</h2><table><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table>')
    for t in st['types']:
        if not t['lines']:
            continue
        lr = [f'<tr><td>{E(_dmy(str(date(st["year"], 1, 1))))}</td><td>{E(tr("Balance brought forward"))}</td><td class="n"></td>'
              f'<td class="n"><span>{t["brought"]:,.2f}</span></td><td></td></tr>']
        for x in t['lines']:
            lr.append(f'<tr><td>{E(_dmy(x["date"]))}</td><td>{E(x["entry"])}</td><td class="n"><span>{x["days"]:+,.2f}</span></td><td class="n"><span>{x["balance"]:,.2f}</span></td>'
                      f'<td>{E(x["note"])}</td></tr>')
        parts.append(f'<h2>{E(tr(t["name"]))}</h2><table><thead><tr><th>{E(tr("Date"))}</th><th>{E(tr("Entry"))}</th><th class="n">{E(tr("Days"))}</th>'
                     f'<th class="n">{E(tr("Balance after"))}</th><th>{E(tr("Note"))}</th></tr></thead><tbody>{"".join(lr)}</tbody></table>')
    return _page(title, head, ''.join(parts))


class LeaveStatementView(APIView):
    """GET dashboard/api/leave-statement/<emp>/?year=2026[&pdf=1] – emp = an employee id, or "me" (self service)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, emp):
        from AccessControl.access import ctx
        c = ctx(request)
        Emp = M('EmpManagement', 'emp_master')
        if emp == 'employees':   # the picker of HR: the employees of their branches (leavers too – their statements are asked for)
            from .reports import REPORTS, _allowed
            if not _allowed(c, REPORTS['leave-statement'][5]):
                return Response({'detail': tr('You may not see the leave statement of other employees.')}, status=status.HTTP_403_FORBIDDEN)
            qs = Emp.objects.all().order_by('emp_first_name', 'emp_last_name')
            if not c.admin and c.branches is not None:
                qs = qs.filter(emp_branch_id__in=c.branches)
            return Response({'rows': [{'id': e['id'], 'name': f"{e['emp_first_name'] or ''} {e['emp_last_name'] or ''}".strip() + f" ({e['emp_code']})",
                                       'active': e['is_active']} for e in qs.values('id', 'emp_first_name', 'emp_last_name', 'emp_code', 'is_active')]})
        if emp == 'me':
            e = c.emp
            if e is None:
                return Response({'detail': tr('Your user is not linked to an employee.')}, status=status.HTTP_404_NOT_FOUND)
        else:
            if not str(emp).isdigit():
                return Response({'detail': tr('Unknown employee.')}, status=status.HTTP_404_NOT_FOUND)
            e = Emp.objects.filter(pk=int(emp)).first()
            if e is None:
                return Response({'detail': tr('Unknown employee.')}, status=status.HTTP_404_NOT_FOUND)
            own = c.emp is not None and c.emp.pk == e.pk
            if not own:
                from .reports import REPORTS, _allowed
                if not _allowed(c, REPORTS['leave-statement'][5]):
                    return Response({'detail': tr('You may not see the leave statement of other employees.')}, status=status.HTTP_403_FORBIDDEN)
                if not c.admin and c.branches is not None and e.emp_branch_id_id not in set(c.branches):
                    return Response({'detail': tr('This employee is not in your branches.')}, status=status.HTTP_403_FORBIDDEN)
        y = request.query_params.get('year') or str(_zt_today().year)
        if not re.match(r'^\d{4}$', y) or not 1990 <= int(y) <= 2100:
            return Response({'detail': tr('Give the year, e.g. 2026.')}, status=status.HTTP_400_BAD_REQUEST)
        st = leave_statement(e, int(y))
        if request.query_params.get('pdf') in ('1', 'true'):
            try:
                pdf = _statement_pdf(request, st)
            except Exception as exc:   # noqa: BLE001
                return Response({'detail': tr('The PDF could not be made: {0}', str(exc)[:200])}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            return _pdf_response(pdf, f'leave-statement-{e.emp_code or e.pk}-{y}.pdf', inline=request.query_params.get('download') != '1')
        return Response(st)
