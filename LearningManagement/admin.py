from django.contrib import admin

from .models import Certificate, Course, Nomination, ParticipantResult, TrainingBond, TrainingNeed, TrainingSession

for m in (Course, TrainingNeed, TrainingSession, Nomination, ParticipantResult, Certificate, TrainingBond):
    admin.site.register(m)
