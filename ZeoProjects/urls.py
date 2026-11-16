"""Projects API – include under /zeo-projects/ in zeo/urls.py:
    path('zeo-projects/', include('ZeoProjects.urls')),
"""
from django.urls import path

from . import dashboard as DB
from . import portal as P
from . import views as V
from . import views_projects as VP
from . import views_service as VS   # v1.67.13 (service)
from . import views_billing as VB   # v1.67.13 (billing)
from . import collection_plan as CP   # v1.67.13 (collections)
from . import support_planner as SP   # v1.67.13 (support planner)
from . import views_field as VF   # v1.67.14 (service releases 3 – 5)

urlpatterns = [
    path('api/lookups/', V.LookupsView.as_view()),
    path('api/settings/', V.SettingsView.as_view()),
    path('api/notification-settings/', V.NotificationSettingsView.as_view()),
    path('api/plan-templates/', VP.PlanTemplateListView.as_view()),
    path('api/plan-templates/defaults/', VP.PlanTemplateDefaultsView.as_view()),
    path('api/plan-templates/<int:pk>/', VP.PlanTemplateDetailView.as_view()),
    path('api/projects/<int:pk>/apply-template/', VP.ProjectApplyTemplateView.as_view()),
    path('api/projects/<int:pk>/save-template/', VP.ProjectSaveTemplateView.as_view()),
    path('api/tasks/<int:task_id>/delete/', VP.TaskDeleteView.as_view()),
    path('api/tasks/<int:task_id>/files/', VP.TaskFilesView.as_view()),
    path('api/task-files/<int:pk>/', VP.TaskFileView.as_view()),
    path('api/my/', DB.MyWorkView.as_view()),
    path('api/dashboard/', DB.DashboardView.as_view()),
    path('api/reports/', V.ReportListView.as_view()),
    path('api/reports/weekly-summary/', V.WeeklySummaryView.as_view()),
    path('api/reports/work-history/file/', V.WorkHistoryFileView.as_view()),
    path('api/reports/work-history/send/', V.WorkHistorySendView.as_view()),
    path('api/reports/<str:key>/', V.ReportView.as_view()),
    # masters
    path('api/masters/<str:kind>/', V.MasterListView.as_view()),
    path('api/masters/<str:kind>/<int:pk>/', V.MasterDetailView.as_view()),
    path('api/customers/', V.CustomerListView.as_view()),
    path('api/customers/<int:pk>/', V.CustomerDetailView.as_view()),
    path('api/contacts/', V.ContactListView.as_view()),
    path('api/contacts/<int:pk>/', V.ContactDetailView.as_view()),
    path('api/contacts/<int:pk>/portal/', V.ContactPortalView.as_view()),
    # v1.67.13 (service): sites, equipment, categories, AMC types, industry packs
    path('api/sites/', VS.SiteListView.as_view()),
    path('api/sites/<int:pk>/', VS.SiteDetailView.as_view()),
    path('api/equipment/', VS.EquipmentListView.as_view()),
    path('api/equipment/import/', VS.EquipmentImportView.as_view()),
    path('api/equipment/<int:pk>/', VS.EquipmentDetailView.as_view()),
    path('api/equipment-categories/', VS.EquipmentCategoryListView.as_view()),
    path('api/equipment-categories/<int:pk>/', VS.EquipmentCategoryDetailView.as_view()),
    path('api/amc-types/', VS.AMCTypeListView.as_view()),
    path('api/amc-types/<int:pk>/', VS.AMCTypeDetailView.as_view()),
    path('api/industries/', VS.IndustryView.as_view()),
    # support
    path('api/tickets/', V.TicketListView.as_view()),
    path('api/tickets/<int:pk>/', V.TicketDetailView.as_view()),
    path('api/tickets/<int:pk>/assign/', V.TicketAssignView.as_view()),
    path('api/tickets/<int:pk>/stage/', V.TicketStageView.as_view()),
    path('api/tickets/<int:pk>/messages/', V.TicketMessageView.as_view()),
    path('api/tickets/<int:pk>/worklogs/', V.TicketWorkLogView.as_view()),
    path('api/tickets/<int:pk>/dev-request/', V.TicketDevRequestView.as_view()),
    path('api/tickets/<int:pk>/approval/', V.TicketApprovalView.as_view()),
    path('api/files/<int:pk>/', V.FileDownloadView.as_view()),
    path('api/worklogs/', V.WorkLogListView.as_view()),
    path('api/worklogs/decide/', V.WorkLogDecideView.as_view()),
    path('api/worklogs/<int:pk>/', V.WorkLogDetailView.as_view()),
    path('api/dev-requests/', V.DevRequestListView.as_view()),
    path('api/dev-requests/<int:pk>/', V.DevRequestDetailView.as_view()),
    path('api/dev-requests/<int:pk>/decide/', V.DevRequestDecideView.as_view()),
    path('api/dev-requests/<int:pk>/progress/', V.DevRequestProgressView.as_view()),
    # AMC
    path('api/contracts/', V.ContractListView.as_view()),
    path('api/contracts/<int:pk>/', V.ContractDetailView.as_view()),
    path('api/contracts/<int:pk>/<str:action>/', V.ContractActionView.as_view()),
    path('api/billing/', V.BillingListView.as_view()),
    # v1.67.13 (billing) PI, tax invoice, payments, follow-ups, collections
    path('api/billing/collections/', VB.CollectionsView.as_view()),
    path('api/billing/items/<str:kind>/<int:pk>/', VB.ItemView.as_view()),
    path('api/billing/items/<str:kind>/<int:pk>/stage/', VB.ItemStageView.as_view()),
    path('api/billing/documents/', VB.DocumentListView.as_view()),
    path('api/billing/documents/<int:pk>/', VB.DocumentDetailView.as_view()),
    path('api/billing/documents/<int:pk>/pdf/', VB.DocumentPdfView.as_view()),
    path('api/billing/documents/<int:pk>/payments/', VB.PaymentView.as_view()),
    path('api/billing/documents/<int:pk>/<str:action>/', VB.DocumentActionView.as_view()),
    path('api/billing/payments/<int:pk>/', VB.PaymentDetailView.as_view()),
    path('api/billing/followups/', VB.FollowUpView.as_view()),
    path('api/billing/users/', VB.BillingUsersView.as_view()),
    # v1.67.13 (collections) collection planner
    path('api/collections/', CP.CollectionLinesView.as_view()),
    path('api/collections/report/', CP.CollectionReportView.as_view()),
    path('api/collections/sources/', CP.CollectionSourcesView.as_view()),
    path('api/collections/<int:pk>/', CP.CollectionLineView.as_view()),
    # v1.67.13 (support planner)
    path('api/support-planner/', SP.PlannerView.as_view()),
    path('api/support-planner/<int:pk>/', SP.AllocationView.as_view()),
    path('api/support-planner/<int:pk>/job/', SP.JobView.as_view()),
    # v1.67.14 (service releases 3 – 5): technician's job, parts, vans, preventive visits, KPIs
    path('api/support-planner/<int:pk>/visit/', VF.JobVisitView.as_view()),
    path('api/support-planner/<int:pk>/parts/', VF.JobPartsView.as_view()),
    path('api/support-planner/<int:pk>/parts/<int:part_pk>/', VF.JobPartsView.as_view()),
    path('api/visits/<int:pk>/pdf/', VF.VisitPdfView.as_view()),
    path('api/tickets/<int:pk>/field/', VF.TicketFieldView.as_view()),
    path('api/field/vans/', VF.VansView.as_view()),
    path('api/field/parts/', VF.PartsPickView.as_view()),
    path('api/field/ppm/', VF.PpmView.as_view()),
    path('api/service-kpi/', VF.ServiceKpiView.as_view()),
    path('api/service-kpi/report/<str:key>/', VF.ServiceReportView.as_view()),
    path('api/billing/<int:pk>/', V.BillingDetailView.as_view()),
    # projects
    path('api/projects/', VP.ProjectListView.as_view()),
    path('api/projects/<int:pk>/', VP.ProjectDetailView.as_view()),
    path('api/projects/<int:pk>/tasks/', VP.ProjectTaskView.as_view()),
    path('api/projects/<int:pk>/milestones/', VP.MilestoneView.as_view()),
    path('api/projects/<int:pk>/change-requests/', VP.ChangeRequestView.as_view()),
    path('api/projects/<int:pk>/risks/', VP.RiskView.as_view()),
    path('api/projects/<int:pk>/expenses/', VP.ExpenseView.as_view()),
    path('api/tasks/<int:task_id>/', VP.TaskDetailView.as_view()),
    path('api/milestones/<int:pk>/', VP.MilestoneDetailView.as_view()),
    path('api/milestones/<int:pk>/status/', VP.MilestoneStatusView.as_view()),
    path('api/change-requests/<int:pk>/decide/', VP.ChangeRequestDecideView.as_view()),
    path('api/risks/<int:pk>/', VP.RiskDetailView.as_view()),
    path('api/expenses/<int:pk>/', VP.ExpenseDetailView.as_view()),
    # customer portal
    path('api/portal/me/', P.PortalMeView.as_view()),
    path('api/portal/tickets/', P.PortalTicketListView.as_view()),
    path('api/portal/tickets/<int:pk>/', P.PortalTicketDetailView.as_view()),
    path('api/portal/files/<int:pk>/', P.PortalFileView.as_view()),
    path('api/portal/contracts/', P.PortalContractsView.as_view()),
    path('api/portal/projects/', P.PortalProjectsView.as_view()),
    path('api/portal/equipment/', P.PortalEquipmentView.as_view()),   # v1.67.13 (service)
    path('api/portal/approvals/', P.PortalApprovalsView.as_view()),
    path('api/portal/invoices/', P.PortalInvoicesView.as_view()),
    path('api/portal/password/', P.PortalPasswordView.as_view()),
]
