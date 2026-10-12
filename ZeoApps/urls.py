from django.urls import path

from . import views as V

urlpatterns = [
    path('api/apps/', V.AppsView.as_view()),
    path('api/apps/<str:key>/activate/', V.ActivateView.as_view()),
    path('api/apps/<str:key>/deactivate/', V.DeactivateView.as_view()),
]
