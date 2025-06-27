from rest_framework import serializers
from .models import(state_mstr,cntry_mstr,TaxSystem,crncy_mstr,document_type,LanguageMaster,Nationality,LanguageSkill,MarketingSkill,ProgrammingLanguageSkill,
                    ReligionMaster)
#STATE SERIALIZER
class StateSerializer(serializers.ModelSerializer):
    class Meta:
        model = state_mstr
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(StateSerializer, self).to_representation(instance)
        if instance.country:  # Check if emp_state_id is not None
            rep['country'] = instance.country.country_name
       
        return rep
#CURRENCY SERIALIZER
class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = crncy_mstr
        fields = '__all__'
#COUNTRY SERIALIZER
class CountrySerializer(serializers.ModelSerializer):
    currency = CurrencySerializer(read_only=True)
    states_set = StateSerializer(many=True, read_only=True)  # 
    state_label = serializers.SerializerMethodField()  # Dynamic label field
    class Meta:
        model = cntry_mstr
        fields = '__all__'
    def get_state_label(self, obj):
        return obj.get_state_label()

class CntryBulkUploadSerializer(serializers.ModelSerializer):  
    file = serializers.FileField(write_only=True) 
    class Meta:
        model = cntry_mstr
        fields = '__all__'

class NationalitySerializer(serializers.ModelSerializer):  
    class Meta:
        model = Nationality
        fields = '__all__'
 
 #ntionality bulk upload
class NationalityBlkUpldSerializer(serializers.ModelSerializer):  
    file = serializers.FileField(write_only=True) 
    class Meta:
        model = Nationality
        fields = '__all__'
class Document_typeSerializer(serializers.ModelSerializer):
    class Meta:
        model = document_type
        fields = '__all__'
# LANGUAGES
class LanguageMasterSerializer(serializers.ModelSerializer):
    # br_created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    # br_updated_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    class Meta:
        model= LanguageMaster
        fields = '__all__'

class LanguageSkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = LanguageSkill
        fields = '__all__'

class MarketingSkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketingSkill
        fields = '__all__'

class ProgrammingLanguageSkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProgrammingLanguageSkill
        fields = '__all__'
class LanguageBlkupldSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True) 
    class Meta:
        model = LanguageSkill
        fields = '__all__'

class MarketingBlkupldSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True) 
    class Meta:
        model = MarketingSkill
        fields = '__all__'

class ProLangBlkupldSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True) 
    class Meta:
        model = ProgrammingLanguageSkill
        fields = '__all__'

class TaxSystemSerializer(serializers.ModelSerializer):
    country_name = serializers.CharField(source="country.country_name", read_only=True)
    class Meta:
        model = TaxSystem
        # fields = '__all__'
        fields = ['id', 'country', 'country_name', 'tax_name', 'tax_percentage', 'is_active']

class ReligionMasterSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReligionMaster
        fields = '__all__'
        
class ReligionMasterBlkupldSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True) 
    class Meta:
        model = ReligionMaster
        fields = '__all__'