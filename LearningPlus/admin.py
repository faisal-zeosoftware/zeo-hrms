from django.contrib import admin

from . import models

for m in (models.TrainingCategory, models.Provider, models.Trainer, models.Venue, models.Skill, models.TrainingBudget):
    admin.site.register(m)
