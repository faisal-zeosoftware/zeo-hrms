from django.urls import path

from . import views as V
from .app import app

urlpatterns = app.urls() + [
    path('api/scan/', V.ScanView.as_view(app=app)),        # v1.75.0
    path('api/labels/', V.LabelsView.as_view(app=app)),
    path('api/doc/<int:pk>/note.pdf', V.NoteView.as_view(app=app)),
    path('api/import/opening/', V.OpeningImportView.as_view(app=app)),
]
