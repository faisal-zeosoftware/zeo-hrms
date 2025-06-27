from import_export import resources,fields
from .models import cntry_mstr,Nationality



class CountryResource(resources.ModelResource):
    class Meta:
        model = cntry_mstr
        fields = ('id','country_code','timezone','country_name')
        import_id_fields = ()

class NationalityResource(resources.ModelResource):
    class Meta:
        model = Nationality
        fields = ('id','N_name')
        import_id_fields = ()