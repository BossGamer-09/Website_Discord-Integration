(function($) {
    $(document).ready(function() {
        function toggleFields($row) {
            // Get the current value of the dropdown for this specific inline row
            var entryType = $row.find('.field-entry_type select').val();

            // Define the field rows
            var $textRow = $row.find('.field-text_content').closest('.form-row');
            var $videoRow = $row.find('.field-video_url').closest('.form-row');
            // Since you grouped image/file fields in tuples, we target their shared parent row
            var $imageRow = $row.find('.field-image_file, .field-image_preview').closest('.form-row');
            var $fileRow = $row.find('.field-file_upload, .field-file_link').closest('.form-row');

            // 1. Hide them all initially
            $textRow.hide();
            $videoRow.hide();
            $imageRow.hide();
            $fileRow.hide();

            // 2. Show the relevant row based on the selection
            if (entryType === 'TEXT') {
                $textRow.show();
            } else if (entryType === 'VIDEO') {
                $videoRow.show();
            } else if (entryType === 'IMAGE') {
                $imageRow.show();
            } else if (entryType === 'FILE') {
                $fileRow.show();
            }
        }

        // Apply logic to existing rows on page load
        // '.inline-related' is the class Django gives to inline containers
        $('.inline-related').each(function() {
            // Only toggle if there's actually a select field in this block
            if ($(this).find('.field-entry_type select').length > 0) {
                toggleFields($(this));
            }
        });

        // Listen for user changes on any entry_type dropdown
        $(document).on('change', '.field-entry_type select', function() {
            var $row = $(this).closest('.inline-related');
            toggleFields($row);
        });

        // Listen for Django's custom event when a new inline is added dynamically
        $(document).on('formset:added', function(event, $row, formsetName) {
            if (formsetName === 'entries') { // 'entries' is the related_name of your inline
                toggleFields($row);
            }
        });
    });
})(django.jQuery);