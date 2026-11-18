"""Ask ZEO – load the Arabic terms of Ask ZEO (AskZeo/data/ar_terms.json, the same file as the hand-off's ar_terms_askzeo.json) into
the language packs, without editing i18n/packs/ar.json. A text that already has a translation keeps it (also one edited on the
Translations screen); the next `load_language_packs` keeps these terms too. Run once after `migrate_schemas` and
`load_language_packs`:

    python manage.py load_askzeo_terms
"""
import json
import os

from django.core.management.base import BaseCommand
from django.db import transaction
from django_tenants.utils import schema_context


class Command(BaseCommand):
    help = 'Load the Arabic terms of Ask ZEO into the language packs (missing ones only).'

    def handle(self, *args, **o):
        from LanguagePack.models import Language, Term, source_hash
        from LanguagePack.services import norm
        path = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'ar_terms.json')
        with open(path, encoding='utf-8') as f:
            terms = json.load(f)
        added = filled = kept = 0
        with schema_context('public'), transaction.atomic():
            lang = Language.objects.filter(code='ar').first()
            if lang is None:
                self.stdout.write(self.style.WARNING('Arabic is not loaded – run load_language_packs first.'))
                return
            for en, ar in terms.items():
                text = norm(en)
                h = source_hash(text)
                row = Term.objects.filter(lang='ar', source_hash=h).first()
                if row is None:
                    Term.objects.create(lang='ar', source=text, source_hash=h, value=ar, module='AskZeo', in_source=True)
                    added += 1
                elif not row.value:
                    row.value = ar
                    row.save(update_fields=['value'])
                    filled += 1
                else:
                    kept += 1
            if added or filled:
                lang.version += 1
                lang.save(update_fields=['version'])
        self.stdout.write(self.style.SUCCESS(f'Ask ZEO Arabic terms: {added} added, {filled} filled, {kept} already translated (kept).'))
