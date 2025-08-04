from django.shortcuts import render
from .models import (state_mstr,crncy_mstr,cntry_mstr,document_type,LanguageMaster,Nationality,LanguageSkill,ProgrammingLanguageSkill,MarketingSkill,TaxSystem,ReligionMaster)
from .serializer import (CountrySerializer,StateSerializer,LanguageMasterSerializer,
                         CurrencySerializer,Document_typeSerializer,CntryBulkUploadSerializer,NationalityBlkUpldSerializer,LanguageBlkupldSerializer,
                         MarketingBlkupldSerializer,LanguageSkillSerializer,MarketingSkillSerializer,ProgrammingLanguageSkillSerializer,
                         ProLangBlkupldSerializer,MarketingBlkupldSerializer,LanguageBlkupldSerializer,TaxSystemSerializer,ReligionMasterBlkupldSerializer,NationalitySerializer,ReligionMasterSerializer)
from . permissions import LanguageMasterPermission
from rest_framework.decorators import action
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework import status,generics,viewsets,permissions
from rest_framework.authentication import SessionAuthentication,TokenAuthentication
from rest_framework.permissions import IsAuthenticated,AllowAny,IsAuthenticatedOrReadOnly,IsAdminUser
from rest_framework.parsers import MultiPartParser, FormParser
import csv
from .permissions import CountryPermission,StatePermission,DocTypePermission,LanguageSkillPermission,MarketingSkillPermission,ProgrammingLanguageSkillPermission
import pandas as pd,openpyxl
# Create your views here.

#STATE CRUD
class StateViewSet(viewsets.ModelViewSet):
    queryset = state_mstr.objects.all()
    serializer_class = StateSerializer
    # authentication_classes = [SessionAuthentication,]
    permission_classes = [StatePermission,] 

    # def list(self, request):
    #     country_id = request.query_params.get('country_id')
    #     if country_id:
    #         states = self.queryset.filter(country_id=country_id)
    #         serializer = self.serializer_class(states, many=True)
    #         return Response(serializer.data)
    #     else:
    #         return Response({"error": "Country ID is required"})
#COUNTRY CRUD
class CountryViewSet(viewsets.ModelViewSet):
    queryset = cntry_mstr.objects.all()
    serializer_class = CountrySerializer
    # authentication_classes = [SessionAuthentication,]
    permission_classes = [CountryPermission,]
    # Custom action to get states for a specific country
    # @action(detail=True, methods=['get'])
    # def states(self, request, pk=None):
    #     country = self.get_object()
    #     states = country.state_mstr_set.all()  # Use the correct related_name
    #     serializer = StateSerializer(states, many=True)
    #     return Response(serializer.data)
    # Custom action to get states for a specific country
    @action(detail=True, methods=['get'])
    def states(self, request, pk=None):
        country = self.get_object()
        states = country.state_mstr_set.all()
        serializer = StateSerializer(states, many=True)

        # Get the dynamic label from the serializer
        state_label = self.get_serializer(country).data.get('state_label')

        response_data = {"states": serializer.data}
        if state_label:  # Only include label if it's set
            response_data["state_label"] = state_label
        return Response(response_data)

class CountryBulkuploadViewSet(viewsets.ModelViewSet):
    queryset = cntry_mstr.objects.all()
    serializer_class = CntryBulkUploadSerializer
    # permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def bulk_upload(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            csv_file = request.FILES['file']
            decoded_file = csv_file.read().decode('latin-1').splitlines()  # Decode using 'latin-1'
            reader = csv.DictReader(decoded_file)

            success_count = 0
            error_count = 0
            errors = []

            for row_number, row in enumerate(reader, start=1):
                country_code = row.get('country_code', '')[:50]  # Get country_code, truncate if too long
                country_name = row.get('country_name', '')[:50]  # Get country_name, truncate if too long
                timezone=row.get('timezone','')[:50]

                if not country_code or not country_name:
                    errors.append(f"Missing required fields in row {row_number}")
                    error_count += 1
                    continue  # Skip this row and proceed to the next one

                try:
                    cntry_mstr.objects.create(
                        country_code=country_code,
                        country_name=country_name,
                        timezone=timezone
                    )
                    success_count += 1
                except Exception as e:
                    errors.append(f"Error in row {row_number}: {str(e)}")
                    error_count += 1

            if error_count > 0:
                return Response({'error': errors}, status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response({'message': f'Bulk upload successful. {success_count} rows added.'}, status=status.HTTP_201_CREATED)
        else:
            return Response({'error': 'No file found'}, status=status.HTTP_400_BAD_REQUEST)
class NationalityViewSet(viewsets.ModelViewSet):
    queryset = Nationality.objects.all()
    serializer_class = NationalitySerializer
    # permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)
#nationality bulkupload
class NationalityBlkupldViewSet(viewsets.ModelViewSet):
    queryset = Nationality.objects.all()
    serializer_class = NationalityBlkUpldSerializer
    # permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def bulk_upload(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            csv_file = request.FILES['file']
            decoded_file = csv_file.read().decode('utf-8').splitlines()
            reader = csv.DictReader(decoded_file)

            try:
                for row in reader:
                    Nationality.objects.create(
                        N_name=row['N_name'],
                        
                    )
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'message': 'Bulk upload successful'}, status=status.HTTP_201_CREATED)
        else:
            return Response({'error': 'No file found'}, status=status.HTTP_400_BAD_REQUEST)


#CURRENCY CRUD+
class CurrencyViewSet(viewsets.ModelViewSet):
    queryset = crncy_mstr.objects.all()
    serializer_class = CurrencySerializer
    # authentication_classes = [SessionAuthentication,]
    

class DocumentViewSet(viewsets.ModelViewSet):
    queryset = document_type.objects.all()
    serializer_class = Document_typeSerializer
    permission_classes = [DocTypePermission,] 
    def get_queryset(self):
        """Return only active document types by default."""
        return document_type.objects.filter(is_active=True)
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False  # Soft delete instead of actual deletion
        instance.save()
        return Response({"message": "Document type deactivated successfully"}, status=status.HTTP_204_NO_CONTENT)

class LanguageViewSet(viewsets.ModelViewSet):
    queryset = LanguageMaster.objects.all()
    serializer_class = LanguageMasterSerializer
    # permission_classes = [LanguageMasterPermission]
    def get_serializer_context(self):
        return {'request': self.request}

class LanguageSkillViewSet(viewsets.ModelViewSet):
    queryset = LanguageSkill.objects.all()
    serializer_class = LanguageSkillSerializer
    # permission_classes = [LanguageSkillPermission]

class MarketingSkillViewSet(viewsets.ModelViewSet):
    queryset = MarketingSkill.objects.all()
    serializer_class = MarketingSkillSerializer
    # permission_classes = [MarketingSkillPermission]

class ProgrammingLanguageSkillViewSet(viewsets.ModelViewSet):
    queryset = ProgrammingLanguageSkill.objects.all()
    serializer_class = ProgrammingLanguageSkillSerializer
    # permission_classes = [ProgrammingLanguageSkillPermission]
class LanguageBlkupldViewSet(viewsets.ModelViewSet):
    queryset = LanguageSkill.objects.all()
    serializer_class = LanguageBlkupldSerializer
    # permissio_classes = [LanguageSkillPermission]
    parser_classes = (MultiPartParser, FormParser)

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def bulk_upload(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            excel_file = request.FILES['file']     
            # Check if the uploaded file is an Excel file
            if not excel_file.name.endswith('.xlsx'):
                return Response({'error': 'Only Excel files (.xlsx) are supported'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                # Read the Excel file using pandas
                df = pd.read_excel(excel_file)
                
                # Iterate through each row in the DataFrame
                for index, row in df.iterrows():
                    # Get the emp_master instance corresponding to the emp_id
                                   
                    # Create a Skills_Master object with the emp_instance
                    LanguageSkill.objects.create(
                        
                        language=row['Language'],                     
                    )
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'Bulk upload successful'}, status=status.HTTP_201_CREATED)
        else:
            return Response({'error': 'No file found'}, status=status.HTTP_400_BAD_REQUEST)

class MarketingBlkupldViewSet(viewsets.ModelViewSet):
    queryset = MarketingSkill.objects.all()
    serializer_class = MarketingBlkupldSerializer
    # permission_classes = [MarketingSkillPermission]
    parser_classes = (MultiPartParser, FormParser)

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def bulk_upload(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            excel_file = request.FILES['file']  
            # Check if the uploaded file is an Excel file
            if not excel_file.name.endswith('.xlsx'):
                return Response({'error': 'Only Excel files (.xlsx) are supported'}, status=status.HTTP_400_BAD_REQUEST)      
            try:
                # Read the Excel file using pandas
                df = pd.read_excel(excel_file)
                # Iterate through each row in the DataFrame
                for index, row in df.iterrows():
                    # Get the emp_master instance corresponding to the emp_id                   
                    # Create a Skills_Master object with the emp_instance
                    MarketingSkill.objects.create(
                        
                        marketing=row['Marketing'],                       
                    )
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'Bulk upload successful'}, status=status.HTTP_201_CREATED)
        else:
            return Response({'error': 'No file found'}, status=status.HTTP_400_BAD_REQUEST)


class ProLangBlkupldViewSet(viewsets.ModelViewSet):
    queryset = ProgrammingLanguageSkill.objects.all()
    serializer_class = ProLangBlkupldSerializer
    # permission_classes = [ProgrammingLanguageSkillPermission]
    parser_classes = (MultiPartParser, FormParser)

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def bulk_upload(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            excel_file = request.FILES['file']
            # Check if the uploaded file is an Excel file
            if not excel_file.name.endswith('.xlsx'):
                return Response({'error': 'Only Excel files (.xlsx) are supported'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                # Read the Excel file using pandas
                df = pd.read_excel(excel_file) 
                # Iterate through each row in the DataFrame
                for index, row in df.iterrows():
                    # Get the emp_master instance corresponding to the emp_id
                    # Create a Skills_Master object with the emp_instance
                    ProgrammingLanguageSkill.objects.create(
                        
                        programming_language=row['Programming Language'],    
                    )
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'Bulk upload successful'}, status=status.HTTP_201_CREATED)
        else:
            return Response({'error': 'No file found'}, status=status.HTTP_400_BAD_REQUEST)
        
class TaxSystemViewSet(viewsets.ModelViewSet):
    queryset = TaxSystem.objects.all()
    serializer_class = TaxSystemSerializer

class ReligionMasterViewSet(viewsets.ModelViewSet):
    queryset = ReligionMaster.objects.all()
    serializer_class = ReligionMasterSerializer
class ReligionMasterBlkupldViewSet(viewsets.ModelViewSet):
    queryset = ReligionMaster.objects.all()
    serializer_class = ReligionMasterBlkupldSerializer
    # permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def bulk_upload(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            excel_file = request.FILES['file']
            # Check if the uploaded file is an Excel file
            if not excel_file.name.endswith('.xlsx'):
                return Response({'error': 'Only Excel files (.xlsx) are supported'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                # Read the Excel file using pandas
                df = pd.read_excel(excel_file) 
                # Iterate through each row in the DataFrame
                for index, row in df.iterrows():
                    # Get the emp_master instance corresponding to the emp_id
                    # Create a Skills_Master object with the emp_instance
                    ReligionMaster.objects.create(
                        
                        religion=row['religion'],    
                    )
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'Bulk upload successful'}, status=status.HTTP_201_CREATED)
        else:
            return Response({'error': 'No file found'}, status=status.HTTP_400_BAD_REQUEST)