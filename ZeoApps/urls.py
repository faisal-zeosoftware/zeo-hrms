from django.urls import path

from . import directory as DR   # v1.67.3 (apps) the employee directory of the company core
from . import views as V

urlpatterns = [
    path('api/apps/', V.AppsView.as_view()),
    path('api/apps/<str:key>/activate/', V.ActivateView.as_view()),
    path('api/apps/<str:key>/deactivate/', V.DeactivateView.as_view()),
    path('api/directory/', DR.DirectoryView.as_view()),               # v1.67.3 (apps)
    path('api/directory/<int:pk>/', DR.DirectoryOneView.as_view()),   # v1.67.3 (apps)
]
