from django.db import models


class cntry_mstr(models.Model):
    country_code  =  models.CharField(max_length=150,unique=True,null=True)
    timezone      =  models.CharField(max_length=100, help_text="Set timezone as per IANA timezone database format, e.g., 'America/New_York'",null=True)
    country_name  =  models.CharField(max_length=50,unique=True)
    is_active     = models.BooleanField(default=True)
    
    def __str__(self):
        return self.country_name
    def get_state_label(self):
        """Return dynamic name for 'states' based on country selection."""
        custom_labels = {
        "Afghanistan": "Provinces",
        "Albania": "Counties",
        "Algeria": "Provinces",
        "American Samoa": "Districts",
        "Andorra": "Parishes",
        "Angola": "Provinces",
        "Anguilla": "Districts",
        "Antarctica": "Territories",
        "Antigua and Barbuda": "Parishes",
        "Argentina": "Provinces",
        "Armenia": "Marzes",
        "Aruba": "Regions",
        "Australia": "Territories",
        "Austria": "States",
        "Azerbaijan": "Economic Regions",
        "Bahamas": "Districts",
        "Bahrain": "Governorates",
        "Bangladesh": "Divisions",
        "Barbados": "Parishes",
        "Belarus": "Regions",
        "Belgium": "Regions",
        "Belize": "Districts",
        "Benin": "Departments",
        "Bermuda": "Parishes",
        "Bhutan": "Dzongkhags",
        "Bolivia": "Departments",
        "Bonaire, Sint Eustatius and Saba": "Municipalities",
        "Bosnia and Herzegovina": "Entities",
        "Botswana": "Districts",
        "Bouvet Island": "Territory",
        "Brazil": "States",
        "British Indian Ocean Territory": "Atolls",
        "Brunei Darussalam": "Districts",
        "Bulgaria": "Provinces",
        "Burundi": "Provinces",
        "Cabo Verde": "Islands",
        "Cambodia": "Provinces",
        "Cameroon": "Regions",
        "Canada": "Provinces",
        "Cayman Islands": "Districts",
        "Central African Republic": "Prefectures",
        "Chad": "Regions",
        "Chile": "Regions",
        "China": "Provinces",
        "Christmas Island": "Shires",
        "Cocos (Keeling) Islands": "Territory",
        "Colombia": "Departments",
        "Comoros": "Islands",
        "Congo (Republic of the)": "Departments",
        "Congo (Democratic Republic of the)": "Provinces",
        "Cook Islands": "Islands",
        "Costa Rica": "Provinces",
        "Côte d'Ivoire": "Districts",
        "Croatia": "Counties",
        "Cuba": "Provinces",
        "Curaçao": "Districts",
        "Cyprus": "Districts",
        "Czech Republic": "Regions",
        "Denmark": "Regions",
        "Djibouti": "Regions",
        "Dominica": "Parishes",
        "Dominican Republic": "Provinces",
        "Ecuador": "Provinces",
        "Egypt": "Governorates",
        "El Salvador": "Departments",
        "Equatorial Guinea": "Provinces",
        "Eritrea": "Regions",
        "Estonia": "Counties",
        "Ethiopia": "Regions",
        "Falkland Islands (Malvinas)": "Islands",
        "Faroe Islands": "Regions",
        "Fiji": "Divisions",
        "Finland": "Regions",
        "France": "Regions",
        "French Guiana": "Regions",
        "French Polynesia": "Islands",
        "French Southern Territories": "Districts",
        "Gabon": "Provinces",
        "Gambia": "Divisions",
        "Georgia": "Regions",
        "Germany": "States",
        "Ghana": "Regions",
        "Gibraltar": "Territory",
        "Greece": "Regions",
        "Greenland": "Municipalities",
        "Grenada": "Parishes",
        "Guadeloupe": "Departments",
        "Guam": "Villages",
        "Guatemala": "Departments",
        "Guernsey": "Parishes",
        "Guinea": "Regions",
        "Guinea-Bissau": "Regions",
        "Guyana": "Regions",
        "Haiti": "Departments",
        "Honduras": "Departments",
        "Hong Kong": "Districts",
        "Hungary": "Counties",
        "Iceland": "Regions",
        "India": "States",
        "Indonesia": "Provinces",
        "Iran": "Provinces",
        "Iraq": "Governorates",
        "Ireland": "Counties",
        "Isle of Man": "Parishes",
        "Israel": "Districts",
        "Italy": "Regions",
        "Jamaica": "Parishes",
        "Japan": "Prefectures",
        "Jersey": "Parishes",
        "Jordan": "Governorates",
        "Kazakhstan": "Regions",
        "Kenya": "Counties",
        "Kiribati": "Islands",
        "Korea (Democratic People's Republic of)": "Provinces",
        "Korea (Republic of)": "Provinces",
        "Kuwait": "Governorates",
        "Kyrgyzstan": "Regions",
        "Lao People's Democratic Republic": "Provinces",
        "Latvia": "Regions",
        "Lebanon": "Governorates",
        "Lesotho": "Districts",
        "Liberia": "Counties",
        "Libya": "Districts",
        "Liechtenstein": "Municipalities",
        "Lithuania": "Counties",
        "Luxembourg": "Districts",
        "Macao": "Regions",
        "Macedonia": "Regions",
        "Madagascar": "Regions",
        "Malawi": "Districts",
        "Malaysia": "States",
        "Maldives": "Atolls",
        "Mali": "Regions",
        "Malta": "Regions",
        "Marshall Islands": "Atolls",
        "Martinique": "Regions",
        "Mauritania": "Regions",
        "Mauritius": "Districts",
        "Mexico": "States",
        "Micronesia (Federated States of)": "States",
        "Moldova": "Districts",
        "Mongolia": "Aimags",
        "Montenegro": "Municipalities",
        "Montserrat": "Parishes",
        "Morocco": "Regions",
        "Mozambique": "Provinces",
        "Myanmar": "States and Regions",
        "Namibia": "Regions",
        "Nauru": "Districts",
        "Nepal": "Provinces",
        "Netherlands": "Provinces",
        "New Caledonia": "Provinces",
        "New Zealand": "Regions",
        "Nicaragua": "Departments",
        "Niger": "Regions",
        "Nigeria": "States",
        "Norway": "Counties",
        "Oman": "Governorates",
        "Pakistan": "Provinces",
        "Palau": "States",
        "Panama": "Provinces",
        "Papua New Guinea": "Provinces",
        "Paraguay": "Departments",
        "Peru": "Regions",
        "Philippines": "Regions",
        "Poland": "Voivodeships",
        "Portugal": "Districts",
        "Puerto Rico": "Municipalities",
        "Qatar": "Municipalities",
        "Romania": "Counties",
        "Russian Federation": "Federal Subjects",
        "Rwanda": "Provinces",
        "Saint Kitts and Nevis": "Parishes",
        "Saudi Arabia": "Regions",
        "Senegal": "Regions",
        "Serbia": "Districts",
        "Seychelles": "Districts",
        "Sierra Leone": "Provinces",
        "Singapore": "Planning Areas",
        "Slovakia": "Regions",
        "Slovenia": "Regions",
        "Solomon Islands": "Provinces",
        "South Africa": "Provinces",
        "Spain": "Autonomous Communities",
        "Sri Lanka": "Provinces",
        "Sudan": "States",
        "Sweden": "Counties",
        "Switzerland": "Cantons",
        "Thailand": "Provinces",
        "Turkey": "Provinces",
        "Ukraine": "Oblasts",
        "United Arab Emirates": "Emirates",
        "United Kingdom": "Counties",
        "United States": "States","Uruguay": "Departments","Venezuela": "States","Vietnam": "Provinces","Zambia": "Provinces", "Zimbabwe": "Provinces",
    }

        return custom_labels.get(self.country_name)  # No default value
#STATE MODEL
class state_mstr(models.Model):

    state_name  = models.CharField(max_length=50)
    is_active   = models.BooleanField(default=True)
    country     = models.ForeignKey('cntry_mstr',on_delete=models.CASCADE)
    def __str__(self):
        return self.state_name

#CURRENCY MODEL
class crncy_mstr(models.Model):
    currency_name    = models.CharField(max_length=50,unique=True)
    currency_code    = models.CharField(max_length=3, unique=True)
    symbol           = models.CharField(max_length=5, blank=True, null=True)
    country = models.OneToOneField(cntry_mstr, on_delete=models.CASCADE, related_name='currency',null=True,blank=True)
    
    def __str__(self):
        return self.currency_name
    
# document master
class document_type(models.Model):
    type_name   = models.CharField(max_length=50,unique=True)
    description = models.CharField(max_length=200)
    is_active   = models.BooleanField(default=True)  # Add is_active field
    def __str__(self):
        return self.type_name
    def save(self, *args, **kwargs):
        if not self.pk:  # Only set is_active=True on creation
            self.is_active = True
        super().save(*args, **kwargs)
    
#LANGUAGE MASTER 
class LanguageMaster(models.Model):
    language = models.CharField(max_length=100)
    def __str__(self):
        return self.language
    
#Nationality
class Nationality(models.Model):
    N_name = models.CharField(max_length=200,null=True)

    def str(self):
        return self.N_name

class ReligionMaster(models.Model):
    religion = models.CharField(max_length=200,null=True)

    def __str__(self):
        return self.religion

class LanguageSkill(models.Model):
    language = models.CharField(max_length=100,null=True,blank =True,default=None)
    
    def __str__(self):
        return f"{self.language }"

class MarketingSkill(models.Model):
    marketing = models.CharField(max_length=100,null=True,blank =True,default=None)

    def __str__(self):
        return f"{self.marketing }"

class ProgrammingLanguageSkill(models.Model):
    programming_language = models.CharField(max_length=100,null=True,blank =True,default=None)

    def __str__(self):
        return f"{self.programming_language }"

class TaxSystem(models.Model):
    country = models.OneToOneField(cntry_mstr, on_delete=models.CASCADE)
    tax_name = models.CharField(max_length=100)  # e.g., VAT, GST
    tax_percentage = models.DecimalField(max_digits=5, decimal_places=2)  # e.g., 5.00 for 5%
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.country.country_name} - {self.tax_name} ({self.tax_percentage}%)"