"""Ask ZEO 1.72.0 (A6) – privacy: what is never sent to the AI, applied to EVERY result before it leaves the server.

* Never sent: IBAN and account numbers, passport, Emirates ID, visa / UID, labour card and Person ID numbers, passwords, keys,
  tokens and secrets (the value is replaced by "[hidden]").
* Salary amounts (company switch "Keep salaries from the AI", on by default): every money value of a salary / pay / cost column
  is replaced by a token ⟦V1⟧, ⟦V2⟧ … The AI writes its answer with the tokens; the server puts the real amounts back into the
  answer the user sees. Totals and groups are added up on the server before the tokens are made, so the AI can still say
  "the total is ⟦V7⟧" without ever seeing a salary.
* Masked names (switch "Mask employee names"): employee names become ⟦N1⟧ … and are put back the same way.
"""
import re
from decimal import Decimal

SECRET_KEY_RX = re.compile(r'(password|passwd|secret|token|api[_ ]?key|otp|hash|salt|private|credential|pin\b)', re.I)
ID_KEY_RX = re.compile(r'(iban|account[_ ]?(no|number)|\baccount\b|passport|emirates|\beid\b|visa|\buid\b|labour|labor|person[_ ]?id|mol[_ ]?id|routing|card[_ ]?no|national[_ ]?id)', re.I)
MONEY_KEY_RX = re.compile(r'(salary|basic|gross|net|pay\b|paid|amount|allowance|deduction|cost|wage|gratuity|liability|total|loan|advance|commission|bonus|'
                          r'arrears|housing|transport|earning|addition|encash|settlement|value|outstanding|repaid|instalment|installment|accrual|provision|rate|'
                          r'pension|gpssa|insurance|iloe|contribution|premium|overtime|reimburse|expense|penalt|incentive)', re.I)
IBAN_RX = re.compile(r'\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b')
EID_RX = re.compile(r'\b784[- ]?\d{4}[- ]?\d{7}[- ]?\d\b')
PASSPORT_RX = re.compile(r'\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{7,9}\b')
TOKEN_RX = re.compile(r'⟦([VN])(\d+)⟧')


class Shield:
    """One per answer: the token tables, so the answer can be put back together."""

    def __init__(self, hide_salary=True, mask_names=False, names=None):
        self.hide_salary, self.mask_names = hide_salary, mask_names
        self.values, self.vrev = {}, {}
        self.names, self.nrev = {}, {}
        self.known_names = sorted({n for n in (names or []) if n and len(n) > 3}, key=len, reverse=True) if mask_names else []
        self.hidden = 0

    # -------------------------------------------------------------- tokens
    def vtok(self, v):
        key = str(v)
        if key not in self.vrev:
            n = len(self.values) + 1
            self.values[n] = v
            self.vrev[key] = n
        return f'⟦V{self.vrev[key]}⟧'

    def ntok(self, name):
        if name not in self.nrev:
            n = len(self.names) + 1
            self.names[n] = name
            self.nrev[name] = n
        return f'⟦N{self.nrev[name]}⟧'

    # -------------------------------------------------------------- text
    def text(self, s):
        if not isinstance(s, str) or not s:
            return s
        s = IBAN_RX.sub(self._hid, s)
        s = EID_RX.sub(self._hid, s)
        for n in self.known_names:
            if n in s:
                s = s.replace(n, self.ntok(n))
        return s

    def _hid(self, _m):
        self.hidden += 1
        return '[hidden]'

    # -------------------------------------------------------------- any result (dict / list / value)
    def protect(self, obj, key=''):
        if isinstance(obj, dict):
            label = str(obj.get('label') or '') if 'label' in obj and 'value' in obj else ''
            out = {}
            for k, v in obj.items():
                kk = f'{label} {k}' if label and k == 'value' else str(k)
                if SECRET_KEY_RX.search(str(k)):
                    self.hidden += 1
                    continue
                out[k] = self.protect(v, kk)
            if label and isinstance(out.get('value'), str) and (ID_KEY_RX.search(label) or SECRET_KEY_RX.search(label)):
                out['value'] = '[hidden]'
                self.hidden += 1
            return out
        if isinstance(obj, (list, tuple)):
            return [self.protect(x, key) for x in obj]
        if isinstance(obj, bool) or obj is None:
            return obj
        if key and (SECRET_KEY_RX.search(key) or ID_KEY_RX.search(key)):
            if obj in ('', 0):
                return obj
            self.hidden += 1
            return '[hidden]'
        if self.hide_salary and key and MONEY_KEY_RX.search(key) and not re.search(r'(count|days|hours|rows|percent|pct|%|headcount|employees|number)', key, re.I):
            if isinstance(obj, (int, float, Decimal)) and not isinstance(obj, bool):
                return self.vtok(obj)
            if isinstance(obj, str) and re.fullmatch(r'-?[\d,]+(\.\d+)?', obj.strip()):
                return self.vtok(obj)
        if isinstance(obj, str):
            return self.text(obj)
        return obj

    # -------------------------------------------------------------- back to the user
    def restore(self, s):
        def put(m):
            kind, n = m.group(1), int(m.group(2))
            if kind == 'V' and n in self.values:
                v = self.values[n]
                try:
                    return f'{Decimal(str(v).replace(",", "")):,.2f}'
                except Exception:
                    return str(v)
            if kind == 'N' and n in self.names:
                return self.names[n]
            return m.group(0)
        return TOKEN_RX.sub(put, s or '')


def scan(text):
    """What the outgoing text still contains that should never leave the server (used by the tests and as a last check)."""
    found = []
    if IBAN_RX.search(text or ''):
        found.append('iban')
    if EID_RX.search(text or ''):
        found.append('emirates id')
    return found
