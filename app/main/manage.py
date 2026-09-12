import sys
import os

argv = sys.argv

from app.main.asgi import application
from django.core.management import execute_from_command_line

# application impicitly sets up django

execute_from_command_line(argv)
