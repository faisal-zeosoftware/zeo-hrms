from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProjectViewSet, ProjectStageViewSet, TaskViewSet, TimeSheetViewSet

router = DefaultRouter()
router.register(r'projects', ProjectViewSet)
router.register(r'stages', ProjectStageViewSet)
router.register(r'tasks', TaskViewSet)
router.register(r'timesheets', TimeSheetViewSet)

urlpatterns = [
    path('api/', include(router.urls)),
    # path('', include(router.urls)),
]