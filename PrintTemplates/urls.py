"""v1.62.0 print – /print-templates/api/…"""
from django.urls import path

from . import views as V

urlpatterns = [
    path('api/meta/', V.MetaView.as_view()),
    path('api/templates/', V.TemplatesView.as_view()),
    path('api/templates/<int:pk>/', V.TemplateView.as_view()),
    path('api/templates/<int:pk>/thumb/', V.ThumbView.as_view()),
    path('api/templates/<int:pk>/versions/', V.VersionsView.as_view()),
    path('api/templates/<int:pk>/restore/', V.RestoreView.as_view()),
    path('api/templates/<int:pk>/reset/', V.ResetView.as_view()),
    path('api/preview/', V.PreviewView.as_view()),
    path('api/records/', V.RecordsView.as_view()),
    path('api/defaults/', V.DefaultsView.as_view()),
    path('api/defaults/<int:pk>/', V.DefaultView.as_view()),
    path('api/branding/', V.BrandingView.as_view()),
    path('api/branding/file/', V.BrandingFileView.as_view()),
    path('api/branding/file/<str:kind>/', V.BrandingImageView.as_view()),
    path('api/print/<str:doc_type>/<int:pk>/', V.PrintView.as_view()),
]
