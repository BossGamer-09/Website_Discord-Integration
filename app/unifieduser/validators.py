from django.core import validators
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _


@deconstructible
class CustomUnicodeUsernameValidator(validators.RegexValidator):
    regex = r"^[\w.\[\]+\- ]+\Z"
    message = _(
        "Enter a valid username. This value may contain only letters, "
        "numbers, and [/]/SPACE/./+/-/_ characters."
    )
    flags = 0
