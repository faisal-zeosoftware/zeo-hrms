from django.urls import path

from . import views as v

urlpatterns = [
    path('api/languages/', v.LanguagesView.as_view()),
    path('api/pack/<str:lang>/', v.PackView.as_view()),
    path('api/me/', v.MeView.as_view()),
    path('api/settings/', v.SettingsView.as_view()),
    path('api/terms/', v.TermsView.as_view()),
    path('api/terms/review/', v.ReviewView.as_view()),
    path('api/terms/import/', v.ImportView.as_view()),
    path('api/terms/export/', v.ExportView.as_view()),
    path('api/terms/<int:pk>/', v.TermView.as_view()),
]
