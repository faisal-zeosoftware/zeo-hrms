"""v1.67.5 (bugs) ESS1 – punch from self service (My profile) with the camera: barcode / QR, or the employee's face.

What already existed and is reused:
* punches: engine.add_punch (one punch per employee and second, "punched less than N minutes ago", kinds in / out / breaks,
  the work day recomputed with the shift) and services.check_punch (the attendance rule's allowed methods, IP list,
  GPS / geofence, selfie) – the same checks as My attendance and the mobile app;
* codes: the employee's Attendance Card / Barcode (emp_master.barcode_number), the employee QR badge (ZQR1…, can be
  cancelled by HR) and the kiosk's site QR (ZSITE1…, changes every 30 s – the "company punch QR").

New:
* Barcode / QR punch: the phone or laptop camera reads the code in the browser (ZXing, already in the app). Accepted: the
  employee's own card number or badge QR, or a kiosk's site QR. Recorded as source "qr", verified_by "qr".
* Face punch: the employee registers the face once (consent text, 3 samples); the browser turns each picture into a face
  descriptor (128 numbers, @vladmandic/face-api, MIT licence, bundled under assets/vendor/face-api – no paid service, no
  picture leaves the phone). At the punch one new descriptor is sent; the server compares it with the stored ones
  (Euclidean distance below the company threshold, default 0.5) – the browser is never told the stored descriptors.
  Recorded as source "biometric", verified_by "face". Only descriptors are kept (AttendancePlus.FaceProfile).
* Company setting (Attendance › Self-service punch, ZeoApps.CompanyOption 'ess_punch'): barcode on / off, face on / off,
  accept own card, accept site QR, match threshold.

Honest limits: the face check has no liveness test (a good photo of the employee held to the camera can match); use it with
the geofence / IP rules, or keep it off. The browser computes the descriptor, so a tampered browser could send a descriptor
made elsewhere – the server only compares numbers.
"""
import math

from django.apps import apps
from django.utils import timezone

from Translations.i18n import tr

from . import engine as E
from . import models as M
from . import services as S

KEY = 'ess_punch'
DIM = 128
MAX_SAMPLES = 5
CONSENT_TEXT = ('I agree that ZEO keeps a numeric description of my face (not a photo) so that I can punch in and out with my face '
                'on this company\'s attendance. It is used for attendance only, it is not shared, and I can remove it at any '
                'time in My profile; HR can also remove it.')
DEFAULTS = {'barcode': True, 'face': True, 'own_card': True, 'site_qr': True, 'threshold': 0.5}


def settings():
    try:
        o = apps.get_model('ZeoApps', 'CompanyOption').objects.filter(key=KEY).first()
        v = dict(o.value or {}) if o else {}
    except Exception:   # noqa: BLE001
        v = {}
    out = dict(DEFAULTS)
    out.update({k: v[k] for k in DEFAULTS if k in v})
    try:
        out['threshold'] = min(0.7, max(0.3, float(out['threshold'])))
    except (TypeError, ValueError):
        out['threshold'] = DEFAULTS['threshold']
    return out


def save_settings(data):
    cur = settings()
    for k in ('barcode', 'face', 'own_card', 'site_qr'):
        if k in data:
            cur[k] = str(data[k]).lower() in ('1', 'true', 'yes', 'on')
    if 'threshold' in data:
        try:
            t = float(data['threshold'])
        except (TypeError, ValueError):
            raise S.Refused(tr('Enter a match threshold between 0.30 and 0.70.'))
        if not 0.3 <= t <= 0.7:
            raise S.Refused(tr('Enter a match threshold between 0.30 and 0.70.'))
        cur['threshold'] = round(t, 2)
    apps.get_model('ZeoApps', 'CompanyOption').objects.update_or_create(key=KEY, defaults={'value': cur})
    return settings()


# ----------------------------------------------------------------------------- descriptors
def _vector(v):
    if not isinstance(v, (list, tuple)) or len(v) != DIM:
        raise S.Refused(tr('The face could not be read. Look straight at the camera in good light and try again.'))
    out = []
    for x in v:
        try:
            f = float(x)
        except (TypeError, ValueError):
            raise S.Refused(tr('The face could not be read. Look straight at the camera in good light and try again.'))
        if not math.isfinite(f) or abs(f) > 2:
            raise S.Refused(tr('The face could not be read. Look straight at the camera in good light and try again.'))
        out.append(round(f, 6))
    if math.sqrt(sum(f * f for f in out)) < 0.1:
        raise S.Refused(tr('The face could not be read. Look straight at the camera in good light and try again.'))
    return out


def distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def register(employee, descriptors, consent, user=None):
    if not consent:
        raise S.Refused(tr('Read the consent text and tick "I agree" to register your face.'))
    if not isinstance(descriptors, (list, tuple)) or not 1 <= len(descriptors) <= MAX_SAMPLES:
        raise S.Refused(tr('Take 1 to 5 pictures of your face.'))
    vs = [_vector(d) for d in descriptors]
    for i in range(len(vs)):   # the samples must be the same face
        for j in range(i + 1, len(vs)):
            if distance(vs[i], vs[j]) > 0.6:
                raise S.Refused(tr('The pictures do not look like the same face. Take them again, one person in front of the camera.'))
    fp, _ = M.FaceProfile.objects.update_or_create(employee_id=employee.id, defaults={
        'descriptors': vs, 'consent_text': tr(CONSENT_TEXT), 'consent_at': timezone.now(),
        'consent_by_id': getattr(user, 'pk', None), 'registered_by_id': getattr(user, 'pk', None)})
    return fp


def remove(employee_id):
    return M.FaceProfile.objects.filter(employee_id=employee_id).delete()[0] > 0


def match(employee, descriptor):
    """(ok, best distance). The stored descriptors never leave the server."""
    fp = M.FaceProfile.objects.filter(employee_id=employee.id).first()
    if fp is None or not fp.descriptors:
        raise S.Refused(tr('Register your face first (My profile › Face punch).'))
    v = _vector(descriptor)
    best = min(distance(v, d) for d in fp.descriptors)
    ok = best < settings()['threshold']
    M.FaceProfile.objects.filter(pk=fp.pk).update(last_distance=round(best, 4), **({'last_match_at': timezone.now()} if ok else {}))
    return ok, round(best, 4)


# ----------------------------------------------------------------------------- codes
def code_check(employee, code):
    """The scanned text → (kiosk or None, how). Raises Refused when it is not this employee's card / badge or a site QR."""
    st = settings()
    code = str(code or '').strip()
    if not code:
        raise S.Refused(tr('No code was read. Hold the card or the QR code in front of the camera.'))
    if code.startswith('ZSITE1.'):
        if not st['site_qr']:
            raise S.Refused(tr('Punching with the company QR code is switched off.'), 403)
        return S.kiosk_from_site_qr(code), 'site QR'
    if not st['own_card']:
        raise S.Refused(tr('Punching with your own card is switched off – scan the company QR code on the kiosk screen.'), 403)
    if code.startswith('ZQR1.'):
        who = S.employee_from_qr(code)
        if who.id != employee.id:
            raise S.Refused(tr('This badge belongs to another employee.'), 403)
        return None, 'badge QR'
    mine = (employee.barcode_number or '').strip()
    if mine and code.upper() == mine.upper():
        return None, 'attendance card'
    E_ = apps.get_model('EmpManagement', 'emp_master')
    if E_.objects.filter(barcode_number__iexact=code).exclude(pk=employee.id).exists():
        raise S.Refused(tr('This card belongs to another employee.'), 403)
    if not mine:
        raise S.Refused(tr('No attendance card / barcode is recorded for you. Ask HR to add it, or scan the company QR code.'))
    raise S.Refused(tr('This code is not your attendance card.'))


def punch(request, employee, method, data):
    """method 'barcode' or 'face' → (punch, created, extra). Same rules as every other self punch."""
    st = settings()
    if method not in ('barcode', 'face'):
        raise S.Refused(tr('Choose barcode or face.'))
    if not st[method]:
        raise S.Refused(tr('This way of punching is switched off by your company.'), 403)
    kind = data.get('kind') or 'auto'
    if kind not in dict(M.PUNCH_KINDS):
        raise S.Refused(tr('Unknown punch type.'))
    rule = E.rule_for(employee)
    lat, lng = data.get('lat'), data.get('lng')
    photo = data.get('photo') or None
    try:
        from calendars.face_utils import convert_base64_to_file
        photo = convert_base64_to_file(photo, 'punch') if photo else None
    except Exception:   # noqa: BLE001
        photo = None
    kiosk, extra = None, {}
    if method == 'barcode':
        kiosk, how = code_check(employee, data.get('code'))
        source, verified, extra['matched'] = 'qr', 'qr', how
    else:
        ok, dist = match(employee, data.get('descriptor'))
        extra['distance'] = dist
        if not ok:
            raise S.Refused(tr('Your face did not match the registered face. Try again in good light, or register your face again.'), 403)
        source, verified = 'biometric', 'face'
    geo = S.check_punch(employee, rule, source, ip=S.client_ip(request), lat=lat, lng=lng, photo=photo)
    p, created = E.add_punch(employee, None, kind, source, kiosk=kiosk, device_ref=str(data.get('device_id') or '')[:64],
                             ip=S.client_ip(request), lat=lat or None, lng=lng or None, location=str(data.get('location') or '')[:255],
                             photo=photo, verified_by=verified, user_id=request.user.id,
                             note=('Self service · ' + (extra.get('matched') or 'face'))[:255])
    p.geofence_ok = geo
    p.save(update_fields=['geofence_ok'])
    return p, created, extra


def status(employee):
    st = settings()
    fp = M.FaceProfile.objects.filter(employee_id=employee.id).first() if employee else None
    rule = E.rule_for(employee) if employee else None
    return {'barcode_on': st['barcode'], 'face_on': st['face'], 'own_card': st['own_card'], 'site_qr': st['site_qr'],
            'has_card': bool(employee and employee.barcode_number), 'face_registered': bool(fp and fp.descriptors),
            'face_samples': len(fp.descriptors) if fp else 0, 'face_registered_at': fp.consent_at if fp else None,
            'consent_text': tr(CONSENT_TEXT), 'next': E.next_kind(employee.id, E.work_date_for(employee, E.now(), rule)) if employee else None,
            'require_gps': bool(rule and (rule.require_gps or rule.require_geofence)), 'require_selfie': bool(rule and rule.require_selfie),
            'methods': (rule.allowed_methods if rule else []) or []}
