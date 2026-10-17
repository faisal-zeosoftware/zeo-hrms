"""v1.25.0 – /mobile/… (public schema: no company needed)."""
from django.urls import path

from . import views as V

urlpatterns = [
    path('api/info/', V.InfoView.as_view()),
    path('api/devices/', V.DevicesView.as_view()),
    path('api/devices/<int:pk>/', V.DeviceView.as_view()),
    path('api/unlock/', V.UnlockView.as_view()),
]
