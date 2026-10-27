"""v1.38.0 – the texts of the app-kit registers (CRM, Property, Maintenance, Asset Management, People) and the HSE registers that
are built on the server: register names, help lines, column / field labels and help, choice labels, action labels and fields,
child tab titles, role names. Written to i18n/kit_strings.json, which tools/i18n/extract.py merges into i18n/source.json so the
language packs can translate them.

    python manage.py i18n_kit_strings [--out i18n/kit_strings.json]
"""
import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand


def _add(bag, text, where):
    t = ' '.join(str(text or '').split())
    if len(t) < 2 or not any(c.isalpha() for c in t):
        return
    bag.setdefault(t, set()).add(where)


def _field_texts(bag, model, name, where):
    try:
        f = model._meta.get_field(name)
    except Exception:
        return
    _add(bag, getattr(f, 'verbose_name', name), where)
    _add(bag, getattr(f, 'help_text', ''), where)
    for ch in (getattr(f, 'choices', None) or []):
        _add(bag, ch[1], where)


def _act_texts(bag, act, where):
    _add(bag, act.label, where)
    _add(bag, getattr(act, 'confirm', ''), where)
    for fld in getattr(act, 'fields', ()) or ():
        if len(fld) > 1:
            _add(bag, fld[1], where)
        if len(fld) > 2 and isinstance(fld[2], str) and fld[2].startswith('choice:'):
            for part in fld[2][7:].split('|'):
                if '=' in part:
                    _add(bag, part.split('=', 1)[1], where)


class Command(BaseCommand):
    help = 'Collect the server-built texts of the kit and HSE registers for the language packs.'

    def add_arguments(self, p):
        p.add_argument('--out', default=os.path.join(settings.BASE_DIR, 'i18n', 'kit_strings.json'))

    def handle(self, *a, **o):
        import importlib
        bag = {}
        from zeo.appkit.engine import REGISTRY
        for label in ('ZeoCRM', 'ZeoProperty', 'ZeoMaintenance', 'ZeoAssets', 'ZeoPeople'):
            try:
                importlib.import_module(f'{label}.app')
            except Exception as e:
                self.stderr.write(f'{label}: {e}')
                continue
            app = REGISTRY.get(label)
            if app is None:
                continue
            where = f'{label}/app.py'
            _add(bag, app.title, where)
            for _, name in getattr(app, 'roles', []):
                _add(bag, name, where)
            for r in app.regs.values():
                for t in (r.label, r.plural, r.group, r.help):
                    _add(bag, t, where)
                for name in set(tuple(r.cols) + tuple(r.form) + tuple(r.filters) + tuple(getattr(r, 'info', ()) or ())):
                    _field_texts(bag, r.model, name, where)
                for act in r.actions:
                    _act_texts(bag, act, where)
                for ch in r.children or ():
                    _add(bag, ch[0], where)
            sm = getattr(app, 'settings_model', None)
            if sm is not None:
                for f in sm._meta.fields:
                    _field_texts(bag, sm, f.name, where)
        try:
            from ZeoHSE import registers as H
            where = 'ZeoHSE/registers.py'
            for r in H.REGS.values():
                for t in (r.label, r.plural, r.group, r.help):
                    _add(bag, t, where)
                for name in set(tuple(r.cols) + tuple(r.form) + tuple(r.filters)):
                    _field_texts(bag, r.model, name, where)
                for act in r.actions:
                    _act_texts(bag, act, where)
                for ch in r.children or ():
                    _add(bag, ch[0], where)
        except Exception as e:
            self.stderr.write(f'HSE: {e}')
        out = [{'text': t, 'where': sorted(w)} for t, w in sorted(bag.items(), key=lambda x: (x[0].lower(), x[0]))]
        os.makedirs(os.path.dirname(o['out']), exist_ok=True)
        with open(o['out'], 'w', encoding='utf-8') as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
            fh.write('\n')
        self.stdout.write(f'{len(out)} register texts written to {o["out"]}')
