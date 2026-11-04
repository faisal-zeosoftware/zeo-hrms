"""v1.67.0 (item 21, roster part) – API of the month roster grid (ShiftPlanner/month_grid.py), mounted at
/shift-planner/api/month-roster/…:
    GET  month-roster/?month=YYYY-MM&branch=&department=      the grid (employees × days, cells, conflicts, rights, settings)
    POST month-roster/cells/          {month, branch, department, cells: [{employee, date, shift | null, off}]}   (drag / one cell)
    POST month-roster/pattern/        {month, branch, department, pattern, employees: [ids], from, to, anchor}
    POST month-roster/copy-last-month/ {month, branch, department, align: date | weekday, overwrite}
    POST month-roster/publish-now/    {month, branch, department}   (setting "Publish without the approval step")
    GET  month-roster/export/?month=&branch=&department=     the grid as .xlsx
    POST month-roster/import/         multipart: file, month, branch, department, commit 0 | 1
    GET / POST month-roster/settings/ {direct_publish, min_rest_days_week, flag_weekend_rest_day}
Send for approval / approve / send back / publish / reopen use the roster's own actions (periods/<id>/<action>/)."""
from rest_framework.response import Response

from . import month_grid as G
from .services import PlanError
from .views import Base
from Translations.i18n import tr


class MonthRosterView(Base):
    def get(self, request):
        return Response(G.grid(self.r, request.GET))


class MonthRosterAction(Base):
    def get(self, request, action):
        if action == 'export':
            return G.export_xlsx(self.r, request.GET)
        if action == 'settings':
            return Response(G.settings())
        raise PlanError(tr('Unknown action.'), status=404)

    def post(self, request, action):
        d, u = request.data, request.user
        if action == 'cells':
            return Response(G.save_cells(self.r, d, u))
        if action == 'pattern':
            return Response(G.apply_pattern(self.r, d, u))
        if action == 'copy-last-month':
            return Response(G.copy_last_month(self.r, d, u))
        if action == 'publish-now':
            return Response(G.publish_now(self.r, d, u))
        if action == 'import':
            f = request.FILES.get('file')
            if f is None:
                raise PlanError(tr('Choose the file to upload.'), problems={'file': 'Required.'})
            return Response(G.import_xlsx(self.r, d, f, u))
        if action == 'settings':
            if not (self.r.admin or self.r.has('change_rosterperiod')):
                raise PlanError(tr('You may not change the roster rules.'), status=403)
            return Response(G.save_settings(d))
        raise PlanError(tr('Unknown action.'), status=404)
