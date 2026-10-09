"""v1.13.0 – Employee self service API, mounted at /self-service/."""
from django.urls import path

from . import views as v

urlpatterns = [
    path('api/meta/', v.MetaView.as_view()),
    path('api/dashboard/', v.DashboardView.as_view()),
    path('api/options/', v.OptionsView.as_view()),
    # profile and change requests
    path('api/profile/', v.MyProfileView.as_view()),
    path('api/profile/change/', v.ProfileChangeView.as_view()),
    path('api/profile/<int:emp_id>/', v.EmployeeProfileHRView.as_view()),
    path('api/change-requests/', v.ChangeRequestsView.as_view()),
    path('api/change-requests/mine/', v.MyChangeRequestsView.as_view()),
    path('api/change-requests/<int:pk>/', v.ChangeRequestView.as_view()),
    path('api/change-requests/<int:pk>/attachments/<int:aid>/', v.ChangeRequestFileView.as_view()),
    path('api/change-requests/<int:pk>/<str:act>/', v.ChangeRequestActionView.as_view()),
    path('api/settings/policies/', v.PolicySettingsView.as_view()),
    # HR letters
    path('api/letters/', v.LettersHRView.as_view()),
    path('api/letters/mine/', v.MyLettersView.as_view()),
    path('api/letters/issue/', v.LetterIssueView.as_view()),
    path('api/letters/verify/', v.LetterVerifyView.as_view()),
    path('api/letters/preview/', v.LetterPreviewView.as_view()),
    path('api/letters/<int:pk>/pdf/', v.LetterPdfView.as_view()),
    path('api/letters/<int:pk>/<str:act>/', v.LetterActionView.as_view()),
    path('api/letter-templates/', v.LetterTemplatesView.as_view()),
    path('api/letter-templates/<int:pk>/', v.LetterTemplateView.as_view()),
    # complaints / grievances
    path('api/complaints/', v.ComplaintsHandlerView.as_view()),
    path('api/complaints/mine/', v.MyComplaintsView.as_view()),
    path('api/complaints/track/', v.ComplaintTrackView.as_view()),
    path('api/complaints/settings/', v.ComplaintSettingsView.as_view()),
    path('api/complaints/escalate/', v.ComplaintEscalateView.as_view()),
    path('api/complaints/<int:pk>/', v.ComplaintView.as_view()),
    path('api/complaints/<int:pk>/messages/<int:mid>/file/', v.ComplaintFileView.as_view()),
    path('api/complaints/<int:pk>/<str:act>/', v.ComplaintActionView.as_view()),
    # announcements
    path('api/announcements/', v.MyAnnouncementsView.as_view()),
    path('api/announcements/stats/', v.AnnouncementStatsView.as_view()),
    path('api/announcements/<int:pk>/stats/', v.AnnouncementStatsView.as_view()),
    path('api/announcements/<int:pk>/read/', v.AnnouncementReadView.as_view()),
    path('api/announcements/<int:pk>/attachment/', v.AnnouncementFileView.as_view()),
    # payslips and documents
    path('api/payslips/', v.MyPayslipsView.as_view()),
    path('api/payslips/<int:pk>/', v.MyPayslipView.as_view()),
    path('api/payslips/<int:pk>/pdf/', v.MyPayslipPdfView.as_view()),
    path('api/documents/', v.MyDocumentsView.as_view()),
    path('api/documents/<int:pk>/file/', v.DocumentFileView.as_view()),
]
