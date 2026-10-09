"""Mounted by LearningManagement/urls.py at /learning/plus/ when the LearningPlus app is installed."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views as V

router = DefaultRouter()
router.register(r'categories', V.CategoryViewSet)
router.register(r'providers', V.ProviderViewSet)
router.register(r'trainers', V.TrainerViewSet)
router.register(r'venues', V.VenueViewSet)
router.register(r'course-extras', V.CourseExtraViewSet)
router.register(r'session-extras', V.SessionExtraViewSet)
router.register(r'modules', V.CourseModuleViewSet)
router.register(r'progress', V.ModuleProgressViewSet)
router.register(r'enrolments', V.OnlineEnrolmentViewSet)
router.register(r'attendance', V.SessionAttendanceViewSet)
router.register(r'budgets', V.TrainingBudgetViewSet)
router.register(r'session-costs', V.SessionCostViewSet)
router.register(r'skills', V.SkillViewSet)
router.register(r'skill-levels', V.SkillLevelViewSet)
router.register(r'course-skills', V.CourseSkillViewSet)
router.register(r'role-skills', V.RoleSkillViewSet)
router.register(r'employee-skills', V.EmployeeSkillViewSet)

urlpatterns = [
    path('api/calendar/', V.CalendarView.as_view()),
    path('api/history/', V.HistoryView.as_view()),
    path('api/skill-matrix/', V.SkillMatrixView.as_view()),
    path('api/course-progress/', V.CourseProgressView.as_view()),
    path('api/my-learning/', V.MyLearningView.as_view()),
    path('api/budget-vs-actual/', V.BudgetView.as_view()),
    path('api/employee-cost/', V.EmployeeCostView.as_view()),
    path('api/certificates/<int:pk>/pdf/', V.CertificateDocView.as_view()),
    path('api/trainers/<int:pk>/stats/', V.TrainerStatsView.as_view()),
    path('api/alerts/run/', V.AlertsView.as_view()),
    path('api/', include(router.urls)),
]
