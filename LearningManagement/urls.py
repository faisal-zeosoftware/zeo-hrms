from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (CertificateViewSet, CourseViewSet, NominationViewSet, ParticipantResultViewSet,
                    TrainingBondViewSet, TrainingNeedViewSet, TrainingSessionViewSet)

router = DefaultRouter()
router.register(r'courses', CourseViewSet)
router.register(r'needs', TrainingNeedViewSet)
router.register(r'sessions', TrainingSessionViewSet)
router.register(r'nominations', NominationViewSet)
router.register(r'results', ParticipantResultViewSet)
router.register(r'certificates', CertificateViewSet)
router.register(r'bonds', TrainingBondViewSet)

urlpatterns = [
    path('api/', include(router.urls)),
]
