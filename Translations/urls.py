from django.urls import path

from . import views as V

urlpatterns = [
    path('api/', V.TranslationListView.as_view()),
    path('api/<int:pk>/', V.TranslationDetailView.as_view()),
    path('api/registry/', V.RegistryView.as_view()),
    path('api/record/', V.RecordView.as_view()),
]
