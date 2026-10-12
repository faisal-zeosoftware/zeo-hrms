from django.contrib import admin

from .models import Contact, CRMSettings, Opportunity, Pipeline, Stage

for m in (CRMSettings, Pipeline, Stage, Contact, Opportunity):
    admin.site.register(m)
