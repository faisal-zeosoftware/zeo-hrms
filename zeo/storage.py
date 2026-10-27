"""v1.62.0 – random file names for new uploads (README-UPDATE 83.1).

A file saved through a file field gets a random part in its name (passport.pdf → passport_k3J9xQ2mTzA7.pdf), so a link cannot
be guessed from an employee code or a document name. The original name stays readable for the person who downloads it.
Company logos (logos/) keep their names – they are public anyway. ZEO_MEDIA_RANDOM_NAMES=false switches it off.
"""
import os
import secrets

from django.conf import settings
from django.core.files.storage import FileSystemStorage

KEEP = ('logos/', 'reports/', 'tmp/')


class ZeoFileStorage(FileSystemStorage):
    def get_available_name(self, name, max_length=None):
        if getattr(settings, 'ZEO_MEDIA_RANDOM_NAMES', True) and not str(name).replace('\\', '/').startswith(KEEP):
            d, base = os.path.split(name)
            root, ext = os.path.splitext(base)
            tag = secrets.token_urlsafe(9).replace('-', 'x').replace('_', 'z')
            room = (max_length or 100) - len(d) - len(ext) - len(tag) - 2
            root = root[:max(8, room)] if room < len(root) else root
            name = os.path.join(d, f'{root}_{tag}{ext}')
        return super().get_available_name(name, max_length=max_length)
