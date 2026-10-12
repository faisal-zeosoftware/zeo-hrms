"""HSE API – include under /zeo-hse/ in zeo/urls.py:
    path('zeo-hse/', include('ZeoHSE.urls')),
"""
from django.urls import path

from . import registers as R
from . import views as V

urlpatterns = [
    path('api/lookups/', V.LookupsView.as_view()),
    path('api/activate/', V.ActivateView.as_view()),   # v1.17.0 HSE activation with its own industry
    path('api/settings/', V.SettingsView.as_view()),
    path('api/pack/', V.PackView.as_view()),
    path('api/pack/review/', V.ReviewView.as_view()),
    path('api/dashboard/', V.DashboardView.as_view()),
    path('api/my/', V.MyHSEView.as_view()),
    path('api/masters/<str:kind>/', V.MasterListView.as_view()),
    path('api/masters/<str:kind>/<int:pk>/', V.MasterDetailView.as_view()),
    path('api/areas/', V.AreaView.as_view()),
    path('api/areas/<int:pk>/', V.AreaView.as_view()),
    path('api/hazards/', V.HazardPickView.as_view()),
    path('api/risks/', V.RiskListView.as_view()),
    path('api/risks/<int:pk>/', V.RiskDetailView.as_view()),
    path('api/risks/<int:pk>/<str:action>/', V.RiskActionView.as_view()),
    path('api/permits/', V.PermitListView.as_view()),
    path('api/permits/<int:pk>/', V.PermitDetailView.as_view()),
    path('api/permits/<int:pk>/pdf/', V.PermitPdfView.as_view()),
    path('api/permits/<int:pk>/gas-tests/', V.GasTestView.as_view()),
    path('api/permits/<int:pk>/isolations/', V.IsolationView.as_view()),
    path('api/isolations/<int:iid>/', V.IsolationView.as_view()),
    path('api/permits/<int:pk>/<str:action>/', V.PermitActionView.as_view()),
    path('api/incidents/', V.IncidentListView.as_view()),
    path('api/incidents/<int:pk>/', V.IncidentDetailView.as_view()),
    path('api/incidents/<int:pk>/<str:action>/', V.IncidentActionView.as_view()),
    path('api/inspections/', V.InspectionListView.as_view()),
    path('api/inspections/<int:pk>/', V.InspectionDetailView.as_view()),
    path('api/observations/', V.ObservationListView.as_view()),
    path('api/toolbox/', V.ToolboxListView.as_view()),
    path('api/actions/', V.ActionListView.as_view()),
    path('api/actions/<int:pk>/', V.ActionDetailView.as_view()),
    path('api/actions/<int:pk>/<str:step>/', V.ActionStepView.as_view()),
    path('api/files/', V.FileListView.as_view()),
    path('api/files/<int:pk>/', V.FileView.as_view()),
    path('api/observations/<int:pk>/escalate/', V.ObservationEscalateView.as_view()),
    path('api/observations/trends/', V.ObservationTrendView.as_view()),
    path('api/observations/<int:pk>/', V.ObservationDetailView.as_view()),
    # v1.17.0 links with the other ZEO apps
    path('api/links/', V.LinksView.as_view()),
    path('api/links/muster/', V.MusterView.as_view()),
    path('api/links/handovers/', V.HandoverView.as_view()),
    path('api/links/handovers/<int:pk>/', V.HandoverView.as_view()),
    path('api/links/costs/', V.CostReportView.as_view()),
    path('api/project/<int:pid>/', V.ProjectHSEView.as_view()),
    # blueprint modules (generic registers)
    path('api/registers/', R.RegistersView.as_view()),
    path('api/options/<str:name>/', R.OptionsView.as_view()),
    path('api/r/<str:kind>/', R.RegisterListView.as_view()),
    path('api/r/<str:kind>/<int:pk>/', R.RegisterDetailView.as_view()),
    path('api/r/<str:kind>/<int:pk>/do/<str:action>/', R.RegisterActionView.as_view()),
    path('api/r/<str:kind>/<int:pk>/files/', R.RegisterFileView.as_view()),
    path('api/r/<str:kind>/<int:pk>/pdf/', R.RegisterPdfView.as_view()),
]
