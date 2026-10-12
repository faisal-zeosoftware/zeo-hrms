"""v1.15.0 – load i18n/packs/<lang>.json (+ i18n/source.json) into the language tables.

    python manage.py load_language_packs                # all packs; keeps translations edited on the screen
    python manage.py load_language_packs --lang ar      # one pack
    python manage.py load_language_packs --force        # the pack file wins over edits made on the screen

Idempotent – run it after every update (deploy step), after `python tools/i18n/extract.py`.
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context

from LanguagePack.services import load_packs


class Command(BaseCommand):
    help = 'Load the language packs (i18n/packs/*.json) into the database.'

    def add_arguments(self, parser):
        parser.add_argument('--lang', action='append', help='only this language (repeatable)')
        parser.add_argument('--force', action='store_true', help='overwrite translations edited on the Translations screen')
        parser.add_argument('--dir', help='pack folder (default: <repo>/i18n/packs)')
        parser.add_argument('--source', help='source.json (default: <repo>/i18n/source.json)')

    def handle(self, *args, **o):
        with schema_context('public'):
            stats = load_packs(force=o['force'], only=o['lang'], pack_dir=o['dir'], source_path=o['source'],
                               log=lambda s: self.stdout.write(s))
        if not stats:
            self.stdout.write(self.style.WARNING('No language pack found (i18n/packs/*.json).'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Language packs loaded: {", ".join(sorted(stats))}.'))
