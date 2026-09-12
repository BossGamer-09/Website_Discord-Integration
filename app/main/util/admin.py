from django.urls import path, reverse
from django.http import JsonResponse
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.utils.html import format_html
from django.db.models import Q
from guardian.shortcuts import get_objects_for_user


class GuardianGenericAutocompleteMixin:
    """
    Admin mixin to provide a Select2 autocomplete endpoint for GenericForeignKeys, along with UI enhancements.
    Needs GFK fields to be default and only supports models with one field per model.
    """
    
    # Customizable Search Field, in case it's not defined by the client model
    # Subclasses can override this to ['slug', 'description'] or whatever makes sense!
    generic_autocomplete_search_fallback_fields = ["name", "title", "slug"]
    generic_autocomplete_search_query_limit = 25

    class Media:
        # Load the Select2 CSS for dropdown
        css = {
            'all': (
                'admin/css/vendor/select2/select2.css',
                'admin/css/autocomplete.css',
            )
        }
        # Load Django's internal jQuery and Select2 JS before custom script
        js = (
            'admin/js/vendor/jquery/jquery.js',
            'admin/js/vendor/select2/select2.full.js',
            'admin/js/jquery.init.js',
            'admin/js/generic_autocomplete.js',
        )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related('related_object')

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'generic-autocomplete/',
                self.admin_site.admin_view(self.generic_autocomplete_view),
                name=f'{self.model._meta.app_label}_{self.model._meta.model_name}_generic_autocomplete',
            ),
        ]
        return custom_urls + urls

    def related_object_link(self, obj):
        """Generates a clickable HTML link to the related object's admin page."""
        related_obj = obj.related_object
        if not related_obj:
            return "-"
            
        opts = related_obj._meta
        try:
            # Dynamically build the admin URL for this specific target model
            url = reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[related_obj.pk])
            return format_html('<a href="{}">{}</a>', url, str(related_obj))
        except Exception:
            # Safe fallback just in case the target model isn't registered in the admin
            return str(related_obj)
            
    related_object_link.short_description = 'Related Object'
    # Allow sorting by content_type in the list view
    related_object_link.admin_order_field = 'content_type'

    def generic_autocomplete_view(self, request):
        content_type_id = request.GET.get('content_type_id')
        search_term = request.GET.get('q', '').strip()

        if not content_type_id:
            return JsonResponse({'results': []})

        try:
            content_type = ContentType.objects.get(pk=content_type_id)
            model_class = content_type.model_class()
        except ContentType.DoesNotExist:
            return JsonResponse({'results': []})

        view_perm = f"{content_type.app_label}.view_{content_type.model}"
        qs = get_objects_for_user(request.user, view_perm, klass=model_class)

        if search_term:
            model_admin = admin.site._registry.get(model_class)
            
            if model_admin and model_admin.search_fields:
                qs, use_distinct = model_admin.get_search_results(request, qs, search_term)
                if use_distinct:
                    qs = qs.distinct()
            else:
                # Dynamic Fallback Logic using Q objects
                q_objects = Q()
                for field in self.generic_autocomplete_search_fallback_fields:
                    # Check if the target model actually has this field before querying
                    if hasattr(model_class, field):
                        # Add an OR condition for this field
                        q_objects |= Q(**{f"{field}__icontains": search_term})
                
                if q_objects:
                    qs = qs.filter(q_objects)
                else:
                    # Ultimate fallback: just search the exact primary key
                    try:
                        qs = qs.filter(pk=search_term) 
                    except (ValueError, TypeError):
                        # user types say a letter but PK is a number
                        qs = qs.none()

        qs = qs[:self.generic_autocomplete_search_query_limit]
        results = [{'id': str(obj.pk), 'text': str(obj)} for obj in qs]
        
        return JsonResponse({'results': results})
