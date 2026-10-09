from django.urls import path

from . import views as v

urlpatterns = [
    path('api/groups/', v.GroupsView.as_view()),
    path('api/profile/<int:emp_id>/', v.ProfileView.as_view()),
    path('api/my-profile/', v.MyProfileView.as_view()),
    path('api/identity/import/', v.IdentityImportView.as_view()),
    path('api/identity/<int:emp_id>/', v.IdentityView.as_view()),
    path('api/doc-sync/<int:emp_id>/', v.DocSyncView.as_view()),
    path('api/employee-columns/', v.EmployeeColumnsView.as_view()),
    path('api/emergency-contacts/', v.EmergencyListView.as_view()),
    path('api/emergency-contacts/<int:pk>/', v.EmergencyDetailView.as_view()),
    path('api/dependents/<int:pk>/', v.DependentView.as_view()),
    path('api/bank-extra/<int:pk>/', v.BankExtraView.as_view()),
    path('api/qualification-extra/<int:pk>/', v.QualificationExtraView.as_view()),
    path('api/iban-check/', v.IbanCheckView.as_view()),
    path('api/eid-check/', v.EidCheckView.as_view()),
    path('api/change/<int:emp_id>/', v.ChangeView.as_view()),
    path('api/employment/<int:emp_id>/', v.EmploymentView.as_view()),
    path('api/employment/<int:emp_id>/<str:act>/', v.ProbationActionView.as_view()),
    path('api/probation/due/', v.ProbationDueView.as_view()),
    path('api/code-settings/', v.CodeSettingsView.as_view()),
    path('api/code-settings/next/', v.NextCodeView.as_view()),
    path('api/code-settings/<int:pk>/', v.CodeSettingDetailView.as_view()),
    path('api/skills/<int:emp_id>/', v.SkillsView.as_view()),
    path('api/history/<int:emp_id>/', v.HistoryView.as_view()),
    path('api/completeness/', v.CompletenessView.as_view()),
    path('api/completeness/<int:emp_id>/', v.CompletenessView.as_view()),
]
