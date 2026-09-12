from django import template

from ..utils import get_user_preference, get_global_preference


register = template.Library()

@register.simple_tag
def global_setting(import_path):
	return get_global_preference(import_path)

@register.simple_tag(takes_context=True)
def user_setting(context, import_path):
	user = context["request"].user
	return get_user_preference(import_path, user)
