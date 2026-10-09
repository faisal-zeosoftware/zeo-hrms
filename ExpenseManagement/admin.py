from django.contrib import admin

from .models import CostCenter, Expense, ExpenseAdvance, ExpenseCategory, ExpensePolicy, ExpenseReport, Trip

for m in (ExpenseCategory, ExpensePolicy, CostCenter, Trip, ExpenseAdvance, Expense, ExpenseReport):
    admin.site.register(m)
