from django.urls import path

from . import views as V

urlpatterns = [
    path('api/categories/', V.CategoriesView.as_view()),               # v1.73.7 payroll categories
    path('api/categories/<int:pk>/', V.CategoryOneView.as_view()),
    path('api/health/', V.HealthView.as_view()),
    path('api/rules/', V.RulesCoverageView.as_view()),
    path('api/employee-salary/', V.EmployeeSalarySetupView.as_view()),     # v1.73.8 salary in New employee                 # v1.73.7 payroll rules and who they apply to
    path('api/templates/<int:pk>/defaults/', V.TemplateDefaultsView.as_view()),   # v1.73.7                        # v1.73.7 payroll health check
    path('api/employees/', V.EmployeesView.as_view()),
    path('api/commission/plans/', V.PlansView.as_view()),
    path('api/commission/entries/', V.EntriesView.as_view()),
    path('api/commission/send/', V.CommissionSendView.as_view()),
    path('api/retention/', V.RetentionView.as_view()),
    path('api/retention/<int:pk>/<str:action>/', V.RetentionActionView.as_view()),
    path('api/service-charge/', V.ServiceView.as_view()),
    path('api/service-charge/<int:pk>/send/', V.ServiceSendView.as_view()),
    path('api/pay-basis/', V.PayBasisView.as_view()),
    path('api/gcc-pension/', V.GCCView.as_view()),
    path('api/iloe/', V.ILOEView.as_view()),
    path('api/emiratisation/', V.EmiratisationView.as_view()),
    path('api/advances/', V.AdvancesView.as_view()),
    path('api/advances/<int:pk>/<str:action>/', V.AdvanceActionView.as_view()),
    path('api/ctc/<int:emp_id>/', V.CTCView.as_view()),
]
