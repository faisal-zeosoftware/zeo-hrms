"""AttendancePlus API – include in zeo/urls.py as path('attendance-plus/', include('AttendancePlus.urls'))."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views as V

router = DefaultRouter()
router.register(r'rules', V.RuleViewSet, basename='attplus-rules')
router.register(r'ip-restrictions', V.IPViewSet, basename='attplus-ip')
router.register(r'devices', V.DeviceViewSet, basename='attplus-devices')
router.register(r'kiosks', V.KioskViewSet, basename='attplus-kiosks')
router.register(r'punches', V.PunchViewSet, basename='attplus-punches')
router.register(r'days', V.DayViewSet, basename='attplus-days')
router.register(r'corrections', V.CorrectionViewSet, basename='attplus-corrections')

urlpatterns = [
    path('api/kiosk/info/', V.KioskInfoView.as_view()),
    path('api/kiosk/punch/', V.KioskPunchView.as_view()),
    path('api/device/push/', V.DevicePushView.as_view()),
    path('api/', include(router.urls)),
]
