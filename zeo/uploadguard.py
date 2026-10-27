"""v1.62.0 – every uploaded file is checked before any screen sees it (README-UPDATE 83.6, "Safe file uploads").

* blocked kinds: programs and scripts (.exe, .bat, .js, .html, .php, .sh …) – by name AND by content (an .exe renamed to .pdf
  is refused as well);
* the real type must match the name: a ".pdf" must start like a PDF, a ".jpg" like a JPEG, a ".xlsx" like an Office file …
  (the first bytes of the file are read – the extension alone proves nothing);
* a size limit per kind (images 15 MB, PDF / Office 30 MB, audio 30 MB, video 200 MB, zip 300 MB); screens keep their own
  smaller limits;
* a virus scan with ClamAV (free) when it runs on the server: ZEO_CLAMAV=auto finds the clamd socket, or give its path;
  ZEO_CLAMAV=off switches the scan off. A company can switch its scan off in Settings › Sign-in security. When clamd cannot
  be reached the file is accepted and the problem is written to the log (System health shows it).
Refused uploads answer 400 with the reason and are written to the security events.
"""
import logging
import os
import socket
import struct

from django.conf import settings

log = logging.getLogger(__name__)
MB = 1024 * 1024

BLOCKED = {'exe', 'dll', 'bat', 'cmd', 'com', 'scr', 'msi', 'msp', 'js', 'mjs', 'jse', 'vbs', 'vbe', 'wsf', 'wsh', 'ps1', 'psm1', 'sh', 'bash',
           'php', 'phtml', 'py', 'pyc', 'pl', 'rb', 'cgi', 'jar', 'class', 'hta', 'html', 'htm', 'xhtml', 'shtml', 'svgz', 'lnk', 'reg',
           'cpl', 'msc', 'gadget', 'apk', 'app', 'deb', 'rpm', 'dmg', 'iso', 'swf', 'asp', 'aspx', 'jsp'}
FAMILY = {
    'pdf': 'pdf',
    'png': 'png', 'jpg': 'jpeg', 'jpeg': 'jpeg', 'jfif': 'jpeg', 'gif': 'gif', 'webp': 'webp', 'bmp': 'bmp', 'tif': 'tiff', 'tiff': 'tiff',
    'heic': 'heic', 'heif': 'heic',
    'docx': 'zip', 'xlsx': 'zip', 'pptx': 'zip', 'xlsm': 'zip', 'odt': 'zip', 'ods': 'zip', 'odp': 'zip', 'zip': 'zip',
    'doc': 'ole', 'xls': 'ole', 'ppt': 'ole', 'msg': 'ole',
    'csv': 'text', 'txt': 'text', 'json': 'text', 'xml': 'text', 'tsv': 'text', 'md': 'text', 'log': 'text', 'ics': 'text', 'vcf': 'text',
    'rtf': 'rtf', 'eml': 'text',
    'mp3': 'mp3', 'wav': 'wav', 'ogg': 'ogg', 'oga': 'ogg', 'opus': 'ogg', 'webm': 'ebml', 'mkv': 'ebml', 'm4a': 'mp4', 'mp4': 'mp4',
    'mov': 'mp4', '3gp': 'mp4', 'aac': 'aac', 'amr': 'amr',
    'svg': 'svg',
}
LIMIT = {'pdf': 30, 'png': 15, 'jpeg': 15, 'gif': 15, 'webp': 15, 'bmp': 15, 'tiff': 30, 'heic': 15, 'zip': 300, 'ole': 30, 'text': 50,
         'rtf': 30, 'mp3': 30, 'wav': 50, 'ogg': 30, 'ebml': 200, 'mp4': 200, 'aac': 30, 'amr': 30, 'svg': 2}
NAMES = {'pdf': 'PDF', 'png': 'PNG image', 'jpeg': 'JPEG image', 'gif': 'GIF image', 'webp': 'WebP image', 'bmp': 'BMP image',
         'tiff': 'TIFF image', 'heic': 'HEIC photo', 'zip': 'Office / zip file', 'ole': 'old Office file', 'text': 'text file',
         'rtf': 'RTF document', 'mp3': 'MP3', 'wav': 'WAV', 'ogg': 'Ogg audio', 'ebml': 'WebM', 'mp4': 'MP4 / M4A',
         'aac': 'AAC audio', 'amr': 'AMR audio', 'svg': 'SVG image'}


class Refused(Exception):
    pass


def _t(text, *a):
    try:
        from Translations.i18n import tr
        return tr(text, *a)
    except Exception:
        return text.format(*a) if a else text


def sniff(head):
    """The family the first bytes belong to, 'program' for executables, or None."""
    h = head or b''
    if h[:2] == b'MZ' or h[:4] == b'\x7fELF' or h[:4] in (b'\xca\xfe\xba\xbe', b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe') or h[:2] == b'#!':
        return 'program'
    if h[:5] == b'%PDF-':
        return 'pdf'
    if h[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if h[:3] == b'\xff\xd8\xff':
        return 'jpeg'
    if h[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if h[:4] == b'RIFF' and h[8:12] == b'WEBP':
        return 'webp'
    if h[:4] == b'RIFF' and h[8:12] == b'WAVE':
        return 'wav'
    if h[:2] == b'BM':
        return 'bmp'
    if h[:4] in (b'II*\x00', b'MM\x00*'):
        return 'tiff'
    if h[4:8] == b'ftyp':
        brand = h[8:12]
        return 'heic' if brand in (b'heic', b'heix', b'mif1', b'msf1', b'hevc', b'heim', b'heis') else 'mp4'
    if h[:4] == b'PK\x03\x04' or h[:4] == b'PK\x05\x06':
        return 'zip'
    if h[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        return 'ole'
    if h[:5] == b'{\\rtf':
        return 'rtf'
    if h[:4] == b'\x1a\x45\xdf\xa3':
        return 'ebml'
    if h[:4] == b'OggS':
        return 'ogg'
    if h[:3] == b'ID3' or h[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'):
        return 'mp3'
    if h[:2] in (b'\xff\xf1', b'\xff\xf9'):
        return 'aac'
    if h[:6] == b'#!AMR\n':
        return 'amr'
    if b'%PDF-' in h[:1024]:   # a PDF with a few bytes before its header (only when no other signature matched – a zip of PDFs is a zip)
        return 'pdf'
    probe = h[:4096].lstrip(b'\xef\xbb\xbf').lstrip()
    low = probe[:400].lower()
    if low.startswith((b'<svg', b'<?xml')) and b'<svg' in probe[:4096].lower():
        return 'svg'
    if low.startswith((b'<!doctype html', b'<html', b'<script')):
        return 'program'
    if h[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return 'text'
    if h and b'\x00' not in h[:4096]:
        try:
            h[:4096].decode('utf-8')
            return 'text'
        except UnicodeDecodeError:
            try:
                h[:4096].decode('utf-16')
                return 'text' if h[:2] in (b'\xff\xfe', b'\xfe\xff') else None
            except UnicodeDecodeError:
                return 'text' if all(b >= 9 for b in h[:512]) else None
    return None


IMAGE = {'png', 'jpeg', 'gif', 'webp', 'bmp', 'tiff', 'heic'}
AUDIO_VIDEO = {'mp3', 'wav', 'ogg', 'ebml', 'mp4', 'aac', 'amr'}
OFFICE_EXT = {'doc', 'docx', 'xls', 'xlsx', 'xlsm', 'ppt', 'pptx', 'odt', 'ods', 'odp'}


def _same(ext, want, fam):
    """The content fits the name: the same family, or a family that people mix up without harm (a PNG saved as .jpg, an RTF
    saved as .doc, a .docx that is an old .doc, an audio file in another container)."""
    if fam == want:
        return True
    if want in IMAGE and fam in IMAGE:
        return True
    if want in AUDIO_VIDEO and fam in AUDIO_VIDEO:
        return True
    if ext in OFFICE_EXT and fam in ('zip', 'ole', 'rtf', 'text' if ext in ('xls',) else 'zip'):
        return True
    if want == 'text' and fam == 'svg' and ext == 'xml':
        return True
    return False


def _head(f, n=8192):
    try:
        pos = f.tell()
    except Exception:
        pos = 0
    try:
        f.seek(0)
        b = f.read(n)
        f.seek(pos if pos else 0)
        return b if isinstance(b, bytes) else str(b).encode()
    except Exception:
        return b''


def _clamd_path():
    v = str(getattr(settings, 'ZEO_CLAMAV', 'auto') or 'auto')
    if v.lower() in ('off', 'false', '0', 'no'):
        return None
    if v.lower() == 'auto':
        for p in ('/var/run/clamav/clamd.ctl', '/run/clamav/clamd.ctl', '/var/run/clamd.scan/clamd.sock', '/tmp/clamd.socket'):
            if os.path.exists(p):
                return p
        return None
    return v


def scan(f):
    """(clean, result) with ClamAV's INSTREAM over its unix socket; (True, 'not scanned') without ClamAV."""
    path = _clamd_path()
    if not path:
        return True, 'not scanned'
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(30)
        s.connect(path)
        s.sendall(b'zINSTREAM\x00')
        f.seek(0)
        while True:
            chunk = f.read(64 * 1024)
            if not chunk:
                break
            s.sendall(struct.pack('!L', len(chunk)) + chunk)
        s.sendall(struct.pack('!L', 0))
        res = b''
        while not res.endswith(b'\x00'):
            part = s.recv(4096)
            if not part:
                break
            res += part
        s.close()
        f.seek(0)
        text = res.rstrip(b'\x00').decode('utf-8', 'replace')
        if text.endswith('FOUND'):
            return False, text.split(':', 1)[-1].replace('FOUND', '').strip()
        return True, text
    except Exception as e:
        log.warning('ClamAV not reachable at %s: %s', path, e)
        try:
            f.seek(0)
        except Exception:
            pass
        return True, f'not scanned ({e})'


def _company_scans():
    try:
        from django.db import connection
        schema = getattr(connection, 'schema_name', 'public')
        if not schema or schema == 'public':
            return True
        from django_tenants.utils import schema_context
        from ZeoCloud.models import SecurityPolicy
        with schema_context('public'):
            v = SecurityPolicy.objects.filter(schema=schema).values_list('upload_scan', flat=True).first()
        return True if v is None else bool(v)
    except Exception:
        return True


def check(f, field=''):
    """Raise Refused with a reason a person understands; returns the family."""
    name = str(getattr(f, 'name', '') or '')
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    size = getattr(f, 'size', 0) or 0
    if ext in BLOCKED:
        raise Refused(_t('“{0}”: files of this kind (.{1}) cannot be uploaded.', name, ext))
    head = _head(f)
    fam = sniff(head)
    if fam == 'program' and ext == 'xls' and head[:2] not in (b'MZ', b'#!') and head[:4] != b'\x7fELF':
        fam = 'text'   # an "Excel" export that is an HTML table (banks, older systems) – served as a download only
    if fam == 'program':
        raise Refused(_t('“{0}” is a program or a web page, not a document – it cannot be uploaded.', name))
    want = FAMILY.get(ext)
    if want is None:
        if fam is None:
            raise Refused(_t('“{0}”: this kind of file is not accepted. Upload a PDF, an image, an Office file, a text / CSV file or a zip.', name))
        want = fam
    elif fam is None or not _same(ext, want, fam):
        if size == 0:
            raise Refused(_t('“{0}” is empty.', name))
        raise Refused(_t('“{0}” is not a real {1} (its content is {2}). Save it again as that type and upload it.', name,
                         _t(NAMES.get(want, ext.upper())), _t(NAMES.get(fam, 'something else'))))
    lim = LIMIT.get(want, 30)
    if size > lim * MB:
        raise Refused(_t('“{0}” is larger than {1} MB.', name, lim))
    if _company_scans():
        clean, res = scan(f)
        if not clean:
            raise Refused(_t('“{0}” was refused by the virus scan ({1}).', name, res))
    return want
