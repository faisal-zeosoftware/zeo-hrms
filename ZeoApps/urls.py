from django.urls import path

from . import directory as DR   # v1.67.3 (apps) the employee directory of the company core
from . import views as V
from . import permgroups as PG   # v1.67.6 (setup) item 10
from . import useremployee as UE   # v1.67.6 (setup) item 9

urlpatterns = [
    path('api/apps/', V.AppsView.as_view()),
    path('api/apps/<str:key>/activate/', V.ActivateView.as_view()),
    path('api/apps/<str:key>/deactivate/', V.DeactivateView.as_view()),
    path('api/apps/<str:key>/setup/', V.AppSetupView.as_view()),        # v1.67.6 (setup) item 5
    path('api/numbering/', V.NumberingView.as_view()),                # v1.67.6 (setup) item 4
    path('api/permission-groups/', PG.GroupsView.as_view()),          # v1.67.6 (setup) item 10
    path('api/permission-catalogue/', PG.CatalogueView.as_view()),    # v1.67.6 (setup) item 10
    path('api/user-employee/', UE.UserEmployeeView.as_view()),        # v1.67.6 (setup) item 9
    path('api/directory/', DR.DirectoryView.as_view()),               # v1.67.3 (apps)
    path('api/directory/<int:pk>/', DR.DirectoryOneView.as_view()),   # v1.67.3 (apps)
]
