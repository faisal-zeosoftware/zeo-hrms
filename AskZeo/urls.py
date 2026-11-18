from django.urls import path

from . import views as V

urlpatterns = [
    path('api/home/', V.HomeView.as_view()),
    path('api/ask/', V.AskView.as_view()),
    path('api/conversations/', V.ConversationsView.as_view()),
    path('api/conversations/<int:pk>/', V.ConversationView.as_view()),
    path('api/uploads/', V.UploadView.as_view()),
    path('api/changes/', V.ChangesView.as_view()),
    path('api/changes/<int:pk>/<str:action>/', V.ChangeView.as_view()),
    path('api/guided/payroll/', V.GuidedPayrollView.as_view()),
    path('api/forms/<str:form>/', V.FormView.as_view()),
    path('api/history/', V.HistoryView.as_view()),
    path('api/usage/', V.UsageView.as_view()),
    path('api/settings/', V.SettingsView.as_view()),
    path('api/documents/', V.DocumentsView.as_view()),                 # 1.72.0
    path('api/documents/<int:pk>/', V.DocumentView.as_view()),
    path('api/uploads/<int:pk>/file/', V.UploadFileView.as_view()),
    path('api/summary/', V.SummaryView.as_view()),
    path('api/test-company/', V.TestCompanyView.as_view()),             # 1.73.5 an app made in the test company first
    path('api/test-company/<slug:key>/', V.TestCompanyView.as_view()),
]
