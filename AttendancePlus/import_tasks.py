"""v1.79.1 – the save of a large punch file runs as a background job (DataTools/import_jobs.py) with the count of punches."""
from DataTools.import_jobs import _put, task


@task('AttendancePlus.import_tasks:punches')
def punches(rows, params, ctx):
    from . import services as S
    commit = params.get('commit', True)
    job = ctx.job

    def day(i, n):   # v1.79.1 then the attendance days of the punches: "Calculating the attendance days 1,240 of 25,000" (the reports show them when done)
        if i == 1 or i % 50 == 0 or i == n:
            job.update({'step': 'days', 'step_done': i, 'step_total': n, 'beat': __import__('time').time()})
            _put(job)
            try:   # this worker still has the job
                from django.core.cache import cache
                from DataTools.import_jobs import _ROW, STALE
                cache.set(_ROW.claim, 1, STALE)
            except Exception:   # noqa: BLE001
                pass
    res = S.import_punches(rows, commit=commit, user_id=ctx.user.pk, allowed_ids=set(params['allowed_ids']) if params.get('allowed_ids') is not None else None,
                           on_row=lambda r: ctx.row(r['status'] != 'error'), on_day=day if commit else None)
    tot = {k: sum(1 for r in res if r['status'] == k) for k in ('ok', 'duplicate', 'error', 'imported')}
    note = res[0].get('note', '') if res else ''
    shown = [r for r in res if r['status'] == 'error'][:1000] + [r for r in res if r['status'] != 'error'][:300]
    shown.sort(key=lambda r: r['row'])
    if not commit:
        return {'committed': False, 'totals': tot, 'rows': shown, 'total_rows': len(res), 'note': note,
                'message': f"{tot['ok']:,} punches ready, {tot['duplicate']:,} already there, {tot['error']:,} with errors."}
    return {'committed': True, 'totals': tot, 'rows': shown, 'total_rows': len(res), 'note': note,
            'message': f"{tot['imported']:,} punches imported" + (f", {tot['duplicate']:,} were there already" if tot['duplicate'] else '')
                       + (f", {tot['error']:,} with errors" if tot['error'] else '') + '.'}
