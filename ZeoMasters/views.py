"""v1.44.0 – partner statement, balances and stock valuation (API for the Masters pages)."""
import csv
from datetime import date
from io import StringIO

from django.http import HttpResponse
from rest_framework.response import Response

from zeo.appkit.engine import AppError, Denied, guard, jsonable
from zeo.appkit.views import Base

from . import accounting as ACC
from . import ledger as LG
from .app import EDIT, app, can_finance
from .models import Partner


def _date(v, field):
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise AppError('Use the date format YYYY-MM-DD.', field)


class StatementView(Base):
    app = app

    @guard
    def get(self, request, pk):
        if not can_finance(request):
            raise Denied('Statements are seen by the managers of the apps and the Masters roles.')
        p = Partner.objects.filter(pk=pk).first()
        if p is None:
            raise AppError('Partner not found.', status=404)
        q = request.query_params
        side = q.get('side') or 'all'
        if side not in ('all', 'receivable', 'payable'):
            side = 'all'
        st = LG.statement(p, _date(q.get('from'), 'from'), _date(q.get('to'), 'to'), side)
        if q.get('export') == 'csv':   # not ?format= (DRF content negotiation)
            buf = StringIO()
            w = csv.writer(buf)
            w.writerow([f'Statement of account – {st["partner"]["name"]} ({st["partner"]["number"]})'])
            w.writerow(['Date', 'App', 'Document', 'Description', 'Due', 'Debit', 'Credit', 'Balance'])
            w.writerow([st['from'] or '', '', '', 'Opening balance', '', '', '', st['opening']])
            for l in st['lines']:
                w.writerow([l['date'], l['app'], l['doc'], l['text'], l['due'] or '', l['debit'] or '', l['credit'] or '', l['balance']])
            w.writerow([st['to'] or '', '', '', 'Closing balance', '', st['debit'], st['credit'], st['closing']])
            r = HttpResponse(buf.getvalue(), content_type='text/csv')
            r['Content-Disposition'] = f'attachment; filename="statement-{st["partner"]["number"]}.csv"'
            return r
        return Response(jsonable(st))


class ValuationView(Base):
    app = app

    @guard
    def get(self, request):
        if not app.any(request, *EDIT, 'mst_viewer'):
            raise Denied('Stock valuation is seen by managers.')
        v = ACC.valuation()
        if request.query_params.get('export') == 'csv':
            buf = StringIO()
            w = csv.writer(buf)
            w.writerow(['Item', 'Code', 'Name', 'Kind', 'Category', 'Unit', 'Method', 'Quantity', 'Unit cost', 'Value', 'Stock account'])
            for r in v['rows']:
                w.writerow([r['number'], r['code'], r['name'], r['kind'], r['category'], r['uom'], r['method'], r['qty'], r['unit_cost'], r['value'], r['stock_account']])
            w.writerow(['', '', 'Total', '', '', '', '', '', '', v['total'], ''])
            resp = HttpResponse(buf.getvalue(), content_type='text/csv')
            resp['Content-Disposition'] = 'attachment; filename="stock-valuation.csv"'
            return resp
        return Response(jsonable(v))


class BalancesView(Base):
    """POST: work out the balances of every partner again (they are also refreshed every night)."""
    app = app

    @guard
    def post(self, request):
        if not app.any(request, *EDIT):
            raise Denied()
        return Response({'partners': LG.refresh_all()})
