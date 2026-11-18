"""v1.62.9 – write the demo workbook (Desert Palm Trading LLC, fictional) made from this server's client template.

    python manage.py make_demo_workbook [--out ZEO_Demo_Data_Desert_Palm_Trading.xlsx]
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Write the demo data workbook (a fictional company that loads into a new company with 0 failed rows).'

    def add_arguments(self, p):
        p.add_argument('--out', default='')

    def handle(self, *a, **o):
        from DataTools import demo_workbook as DW
        from DataTools.workbook_jobs import TEMPLATE_PATH
        out = o['out'] or DW.FILE_NAME
        with open(out, 'wb') as fh:
            fh.write(DW.build(TEMPLATE_PATH))
        self.stdout.write(f'Demo workbook written: {out}')
