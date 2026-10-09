from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views as v

router = DefaultRouter()
router.register(r'locations', v.LocationViewSet, basename='org-location')
router.register(r'divisions', v.DivisionViewSet, basename='org-division')
router.register(r'sections', v.SectionViewSet, basename='org-section')
router.register(r'cost-centers', v.CostCenterViewSet, basename='org-costcenter')
router.register(r'grades', v.GradeViewSet, basename='org-grade')
router.register(r'positions', v.PositionViewSet, basename='org-position')
router.register(r'employment-types', v.EmploymentTypeViewSet, basename='org-employmenttype')
router.register(r'country-policies', v.CountryPolicyViewSet, basename='org-countrypolicy')

urlpatterns = [
    path('api/settings/', v.SettingsView.as_view()),
    path('api/employee-org/', v.EmployeeOrgView.as_view()),
    path('api/employee-org/bulk/', v.EmployeeOrgBulkView.as_view()),
    path('api/employee-org/import/', v.EmployeeOrgImportView.as_view()),
    path('api/employee-org/<int:emp_id>/', v.EmployeeOrgView.as_view()),
    path('api/my-org/', v.MyOrgView.as_view()),
    path('api/hierarchy/', v.HierarchyView.as_view()),
    path('api/hierarchy/check/', v.HierarchyCheckView.as_view()),
    path('api/hierarchy/no-manager/', v.NoManagerView.as_view()),
    path('api/policies/', v.PolicyStatusView.as_view()),
    path('api/policies/<int:pk>/', v.PolicyStatusView.as_view()),
    path('api/policies/<int:pk>/remind/', v.PolicyRemindView.as_view()),
    path('api/my-policies/', v.MyPoliciesView.as_view()),
    path('api/my-policies/<int:pk>/acknowledge/', v.MyPolicyAckView.as_view()),
    path('api/my-policies/<int:pk>/file/', v.MyPolicyFileView.as_view()),
    path('api/branch-country-policies/', v.BranchCountryPolicyView.as_view()),
    path('api/headcount/', v.HeadcountView.as_view()),
    path('api/', include(router.urls)),
]
