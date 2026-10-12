from django.urls import path

from . import views as v

# v1.16.0 – mounted at /ai/ (zeo/urls.py: path('ai/', include('AISettings.urls')))
urlpatterns = [
    path('api/settings/', v.SettingsView.as_view()),
    path('api/settings/test/', v.TestView.as_view()),
    path('api/status/', v.StatusView.as_view()),
    path('api/documents/read/', v.DocumentReadView.as_view()),
]
