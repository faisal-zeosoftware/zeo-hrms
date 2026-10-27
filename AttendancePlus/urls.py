"""AttendancePlus API – include in zeo/urls.py as path('attendance-plus/', include('AttendancePlus.urls'))."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views as V
from . import register62 as R62   # v1.62.0 people

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
    path('api/kiosk/ess/', V.KioskEssView.as_view()),             # v1.51.0 self-service on the kiosk
    path('api/kiosk-pins/', V.KioskPinsView.as_view()),           # v1.51.0 HR sets kiosk PINs
    path('api/regularisation-reasons/', V.RegularisationReasonsView.as_view()),   # v1.49.0
    path('api/ot-law/', V.OtLawView.as_view()),
    path('api/ot-breakdown/', V.OtBreakdownView.as_view()),
    path('api/punch-photos/', V.PunchPhotosView.as_view()),
    path('api/device/push/', V.DevicePushView.as_view()),
    # v1.62.0 people: overtime and late in / early out registers (approval queue, names, h:mm, stage)
    path('api/overtime/', R62.OvertimeView.as_view()),
    path('api/overtime/stats/', R62.OvertimeStatsView.as_view()),
    path('api/overtime/<int:pk>/<str:action>/', R62.OvertimeActionView.as_view()),
    path('api/late-early/', R62.LateEarlyView.as_view()),
    path('api/late-early/stats/', R62.LateEarlyStatsView.as_view()),
    path('api/late-early/<int:pk>/<str:action>/', R62.LateEarlyActionView.as_view()),
    path('api/', include(router.urls)),
]
