"""v1.67.24 (L-BUG-20) – the end of a probation given in days.

Probation is set in days (branch "Probation (days)", employment type), but HR means months: the setup guide stores "6 months" as
180 days. 180 days after 1 January is 30 June, not 1 July – every confirmation date of a 6-month probation was a day or more early
(up to 4 days for a probation over February). A whole number of months (a multiple of 30 days) now counts in calendar months;
any other number of days counts in days as before.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta


def probation_end(joined, days):
    if not joined or days is None:
        return None
    days = int(days)
    if days > 0 and days % 30 == 0:
        return joined + relativedelta(months=days // 30)
    return joined + timedelta(days=days)
