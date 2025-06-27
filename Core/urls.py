from django.urls import path, include
from .  views import (CountryViewSet,StateViewSet,CurrencyViewSet,LanguageViewSet,
                      DocumentViewSet,CountryBulkuploadViewSet,NationalityBlkupldViewSet,LanguageSkillViewSet,MarketingSkillViewSet,ProgrammingLanguageSkillViewSet,LanguageBlkupldViewSet,MarketingBlkupldViewSet,
                    ProLangBlkupldViewSet,TaxSystemViewSet,NationalityViewSet,ReligionMasterViewSet,ReligionMasterBlkupldViewSet)

from rest_framework.routers import DefaultRouter
router = DefaultRouter()

router.register(r'Country', CountryViewSet)
router.register(r'State', StateViewSet)
router.register(r'Bulk-Upload', CountryBulkuploadViewSet,basename='bulk_upload')
router.register(r'tax-system', TaxSystemViewSet,basename='tax_system')
router.register(r'Nationality', NationalityViewSet,basename="nationality")
router.register(r'Nationality-Bulkupload', NationalityBlkupldViewSet,basename="nationality_bulkupload")
router.register(r'religion', ReligionMasterViewSet,basename='religion')
router.register(r'Religion-Bulkupload', ReligionMasterBlkupldViewSet,basename="religion_bulkupload")
router.register(r'Currency', CurrencyViewSet)
router.register(r'Documents', DocumentViewSet)
router.register(r'language', LanguageViewSet)
router.register(r'language_skill', LanguageSkillViewSet, basename='employee-language_skill')
router.register(r'marketing-skill', MarketingSkillViewSet, basename='employee-marketing_skill')
router.register(r'programming-skill', ProgrammingLanguageSkillViewSet, basename='employee-programming_skill')
router.register(r'language_bulkupload', LanguageBlkupldViewSet, basename='employee-language_bulkupload')
router.register(r'marketing_bulkupload', MarketingBlkupldViewSet, basename='employee-marketing_bulkupload')
router.register(r'programming_bulkupload', ProLangBlkupldViewSet, basename='employee-programming_bulkupload')

urlpatterns = [
    # Other paths
    path('api/', include(router.urls)),
    # path('api/CountryBulkuploadViewSet')

]