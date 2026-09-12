(function($) {
    $(document).ready(function() {
        console.log("Mixin Autocomplete Script is running!"); 

        // Target object_id fields (whether they are inputs or already selects)
        const objectIdInputs = $('input[name$="object_id"], select[name$="object_id"]');
        console.log("Found " + objectIdInputs.length + " object_id fields.");

        const pathParts = window.location.pathname.split('/');
        const baseUrl = pathParts.slice(0, 4).join('/'); 
        const ajaxUrl = baseUrl + '/generic-autocomplete/';

        objectIdInputs.each(function() {
            let objectIdInput = $(this);
            
            const inputId = objectIdInput.attr('id');
            if (!inputId) return; 
            
            const contentTypeId = inputId.replace('object_id', 'content_type');
            const contentTypeSelect = $('#' + contentTypeId);

            if (contentTypeSelect.length === 0) return;

            // --- THE FIX: Transform <input type="text"> into <select> ---
            if (objectIdInput.is('input')) {
                console.log("Transforming text input to select element for Select2 v4 compatibility...");
                const select = $('<select></select>');
                select.attr('id', objectIdInput.attr('id'));
                select.attr('name', objectIdInput.attr('name'));
                select.attr('class', objectIdInput.attr('class') + ' admin-autocomplete');
                
                // If editing an existing note, preserve the current value
                const existingValue = objectIdInput.val();
                if (existingValue) {
                    select.append(new Option("Selected ID: " + existingValue, existingValue, true, true));
                }
                
                objectIdInput.replaceWith(select);
                objectIdInput = select; // Update our reference to the new element
            }

            // Initialize Select2
            objectIdInput.select2({
                theme: 'admin-autocomplete',
                
                // 1. ENABLE FREE TEXT ENTRY
                tags: true, 
                
                // 2. FORMAT THE FALLBACK OPTION
                createTag: function (params) {
                    var term = $.trim(params.term);
                    if (term === '') {
                        return null;
                    }
                    // This creates a custom dropdown option on the fly using whatever you typed
                    return {
                        id: term,
                        text: 'Use literal ID: ' + term,
                        newTag: true // Marks it as a user-created tag
                    };
                },
                
                ajax: {
                    url: ajaxUrl,
                    dataType: 'json',
                    delay: 250,
                    data: function (params) {
                        return {
                            q: params.term || '',
                            content_type_id: contentTypeSelect.val()
                        };
                    },
                    processResults: function (data) {
                        return { results: data.results };
                    }
                },
                minimumInputLength: 0,
                placeholder: 'Search or paste an exact ID...',
                allowClear: true,
                width: 'auto'
            });

            // UX: Disable the object input if no content type is selected yet
            function toggleSelect2() {
                const ctValue = contentTypeSelect.val();
                console.log("Current Content Type Value:", ctValue);
                
                if (!ctValue) {
                    objectIdInput.prop('disabled', true);
                } else {
                    objectIdInput.prop('disabled', false);
                }
            }

            toggleSelect2();

            // When the Content Type dropdown changes...
            contentTypeSelect.on('change', function() {
                console.log("Content Type was changed!");
                objectIdInput.val(null).trigger('change');
                toggleSelect2();
            });
        });
    });
})(django.jQuery);
