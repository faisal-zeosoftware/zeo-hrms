"""v1.67.23 (pending – other GCC invoice rules) – what a tax invoice of Projects billing shows in each GCC country.

The country is the company's country (Country pack). Checked November 2026 (two sources each, README-UPDATE 147.4):
  * UAE – VAT 5 %, "Tax Invoice", the supplier's and customer's TRN (as before).
  * Saudi Arabia – VAT 15 %; the invoice must be in Arabic (other languages as a translation – VAT Implementing Regulations
    Art. 53); title "Tax Invoice" / "فاتورة ضريبية"; seller and buyer VAT number (15 digits); a simplified tax invoice (customer
    without a VAT number) carries a QR code with five TLV fields – seller name, VAT number, time stamp, total with VAT, VAT
    (ZATCA e-invoicing phase 1). Phase 2 (XML, cryptographic stamp, clearance / reporting through FATOORA) needs the
    taxpayer's onboarding with ZATCA – not done by ZEO: issue the tax invoices from the accounting system connected to FATOORA.
  * Bahrain – VAT 10 %; "Tax Invoice" (Arabic and / or English); supplier's VAT registration number, customer's when registered.
  * Oman – VAT 5 %; "Tax Invoice"; supplier's VAT registration number; the VAT also in Omani rial when invoiced in another
    currency (Executive Regulations Art. 144).
  * Qatar, Kuwait – no VAT (none introduced as of 2026): "Invoice", no VAT columns.
"""
import base64
from decimal import Decimal

RULES = {
    'AE': {'vat': Decimal('5'), 'title': 'Tax Invoice', 'title_ar': 'فاتورة ضريبية', 'tax_label': 'TRN', 'currency': 'AED',
           'arabic': False, 'qr': False, 'has_vat': True},
    'SA': {'vat': Decimal('15'), 'title': 'Tax Invoice', 'title_ar': 'فاتورة ضريبية', 'title_simplified': 'Simplified Tax Invoice',
           'title_simplified_ar': 'فاتورة ضريبية مبسطة', 'tax_label': 'VAT No.', 'tax_label_ar': 'الرقم الضريبي', 'currency': 'SAR',
           'arabic': True, 'qr': True, 'has_vat': True},
    'BH': {'vat': Decimal('10'), 'title': 'Tax Invoice', 'title_ar': 'فاتورة ضريبية', 'tax_label': 'VAT No.', 'currency': 'BHD',
           'arabic': False, 'qr': False, 'has_vat': True},
    'OM': {'vat': Decimal('5'), 'title': 'Tax Invoice', 'title_ar': 'فاتورة ضريبية', 'tax_label': 'VATIN', 'currency': 'OMR',
           'arabic': False, 'qr': False, 'has_vat': True},
    'QA': {'vat': Decimal('0'), 'title': 'Invoice', 'title_ar': 'فاتورة', 'tax_label': 'Tax No.', 'currency': 'QAR',
           'arabic': False, 'qr': False, 'has_vat': False},
    'KW': {'vat': Decimal('0'), 'title': 'Invoice', 'title_ar': 'فاتورة', 'tax_label': 'Tax No.', 'currency': 'KWD',
           'arabic': False, 'qr': False, 'has_vat': False},
}
AR_LABELS = {   # Saudi Arabia: the Arabic beside the English words of the invoice
    'Bill to': 'العميل', 'Date': 'التاريخ', 'Date of supply': 'تاريخ التوريد', 'Due date': 'تاريخ الاستحقاق', 'Currency': 'العملة',
    'Description': 'الوصف', 'Qty': 'الكمية', 'Unit price': 'سعر الوحدة', 'Amount': 'المبلغ', 'VAT %': 'نسبة الضريبة', 'VAT': 'الضريبة',
    'Total': 'الإجمالي', 'Total before VAT': 'الإجمالي قبل الضريبة', 'Invoice number': 'رقم الفاتورة',
}


def country():
    """the company's country code (Country pack: AE, SA, BH, OM, QA, KW …); AE when it cannot be read"""
    try:
        from CountryPack.services import current_pack
        p = current_pack(None)
        cc = (p.get('country_code') or '').upper() if p.get('pack') != 'GEN' else (p.get('_company_country_code') or '').upper()
        return cc or 'AE'
    except Exception:   # noqa: BLE001
        return 'AE'


def rules(cc=None):
    """the invoice rules of a country (the UAE's for a country outside the GCC – the layout used so far)"""
    return dict(RULES.get((cc or country()).upper(), RULES['AE']), country=(cc or country()).upper())


def default_vat_rate(cc=None):
    return rules(cc)['vat']


def _tlv(tag, value):
    b = str(value).encode('utf-8')
    if len(b) > 255:
        b = b[:255]
    return bytes([tag, len(b)]) + b


def zatca_qr(seller, vat_no, when, total, vat):
    """ZATCA phase 1 QR payload: TLV tags 1–5 (seller name, VAT number, time stamp ISO 8601, total with VAT, VAT), base64"""
    stamp = when.strftime('%Y-%m-%dT%H:%M:%SZ') if hasattr(when, 'strftime') else str(when)
    raw = (_tlv(1, seller) + _tlv(2, vat_no) + _tlv(3, stamp) + _tlv(4, f'{Decimal(total):.2f}') + _tlv(5, f'{Decimal(vat):.2f}'))
    return base64.b64encode(raw).decode('ascii')


def read_qr(payload):
    """→ {tag: value} (for checks)"""
    raw, out, i = base64.b64decode(payload), {}, 0
    while i + 2 <= len(raw):
        tag, n = raw[i], raw[i + 1]
        out[tag] = raw[i + 2:i + 2 + n].decode('utf-8')
        i += 2 + n
    return out
