from django.apps import apps
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

urlpatterns = []
if apps.is_installed('LearningPlus'):
    # masters, online courses, attendance sheet, budget, skills ... -> /learning/plus/api/...
    from LearningPlus.views import CalendarView, CertificateDocView, HistoryView
    urlpatterns += [
        path('api/history/', HistoryView.as_view()),
        path('api/calendar-events/', CalendarView.as_view()),
        path('api/certificates/<int:pk>/pdf/', CertificateDocView.as_view()),
        path('plus/', include('LearningPlus.urls')),
    ]
urlpatterns += [
    path('api/', include(router.urls)),
]
