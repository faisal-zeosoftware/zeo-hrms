from django.contrib import admin

from .models import AssetCard, AssetSettings, Audit, Category, DepreciationRun, Location, Reservation

for m in (AssetSettings, Category, Location, AssetCard, Reservation, Audit, DepreciationRun):
    admin.site.register(m)
