from django.contrib import admin

from .models import AccountMap, DimensionMap, JournalBatch

for m in (AccountMap, DimensionMap, JournalBatch):
    admin.site.register(m)
