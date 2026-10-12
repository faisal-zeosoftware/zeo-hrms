from django.urls import path

from . import views as v

urlpatterns = [
    path('api/current/', v.CurrentView.as_view()),
    path('api/packs/', v.PacksView.as_view()),
    path('api/settings/', v.SettingsView.as_view()),
    path('api/apply-defaults/', v.ApplyDefaultsView.as_view()),
]
