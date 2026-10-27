"""v1.62.0 print – the calls other apps make to print with the company's design. Each returns None when the company has no default
template for the document (or the PDF engine is not installed, or anything fails) – the caller then makes its old PDF.

    render_print(doc_type, obj, request=None, lang=None) -> bytes | None     any document type (see contexts.DOC_TYPES)
    payslip_response(request, payslip) -> HttpResponse | None               payslip downloads (admin, payroll, self service)
    payslip_pdf(payslip, lang=None) -> bytes | None                         e-mail attachment
    letter_pdf(emp, letter_request, letter_template, vals) -> bytes | None  HR letters (the wording of the letter template fills the body)
    settlement_pdf(case, request=None) -> bytes | None                      final settlement statement (case of the Leavers workspace)
    handover_pdf(o) / warning_pdf(o) -> bytes | None                        ZeoHR asset handover / disciplinary case
"""
import logging

log = logging.getLogger(__name__)


def render_print(doc_type, obj, request=None, lang=None, branch_id=None):
    try:
        from . import services as S
        return S.render(doc_type, obj, request, lang, branch_id=branch_id)
    except Exception:   # noqa: BLE001 – never break the old PDF path
        log.exception('print template %s failed – the old PDF is used', doc_type)
        return None


def _lang(request):
    """'en', 'ar' or 'bi' (English and Arabic) for the payslip, as the old payslip chooses it."""
    want = ''
    try:
        want = (getattr(request, 'GET', None) or {}).get('lang', '') if request is not None else ''
        if not want:
            from PayrollPlus.models import PayrollPlusSettings
            want = PayrollPlusSettings.get().payslip_language
    except Exception:   # noqa: BLE001
        pass
    if want == 'en_ar':
        return 'bi'
    if want in ('en', 'ar'):
        return want
    try:
        from Translations.i18n import current_lang
        lg = current_lang(request)
        return 'ar' if str(lg or '').startswith('ar') else 'en'
    except Exception:   # noqa: BLE001
        return 'en'


def payslip_pdf(payslip, lang=None, request=None):
    if request is not None and (getattr(request, 'GET', None) or {}).get('legacy'):
        return None
    return render_print('payslip', payslip, request, lang or _lang(request))


def payslip_response(request, payslip):
    data = payslip_pdf(payslip, request=request)
    if data is None:
        return None
    from django.http import HttpResponse
    r = HttpResponse(data, content_type='application/pdf')
    run = payslip.payroll_run
    r['Content-Disposition'] = f'attachment; filename="payslip_{payslip.employee.emp_code}_{run.year}{run.month:02d}.pdf"'
    return r


def letter_pdf(emp, lr, tpl, vals):
    try:
        from . import contexts as C
        from . import services as S
        from .engine import available
        if not available():
            return None
        S.ensure_seeded()
        doc_type = C.LETTER_DOC.get(lr.letter_type)
        if not doc_type:
            return None
        t = S.default_for(doc_type, emp.emp_branch_id_id, lr.language)
        if t is None:
            return None
        ctx = C.letter_ctx(lr, t.language, tpl, vals, emp)
        return S.pdf(t, ctx)
    except Exception:   # noqa: BLE001
        log.exception('print template for HR letter failed – the old letter is used')
        return None


def settlement_pdf(case, request=None):
    lang = None
    try:
        from Translations.i18n import current_lang
        lang = 'ar' if str(current_lang(request) or '').startswith('ar') else 'en'
    except Exception:   # noqa: BLE001
        pass
    return render_print('final_settlement', case, request, lang)


def handover_pdf(o):
    return render_print('asset_handover', o)


def warning_pdf(o):
    return render_print('warning_letter', o)
