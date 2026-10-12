"""python manage.py zeocrm_setup [--schema X] [--roles] [--activate [--industry property_management]] [--daily]"""
from zeo.appkit.command import make_command

Command = make_command('ZeoCRM')
