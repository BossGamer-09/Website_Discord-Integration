from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm, UsernameField

from django_otp.forms import OTPAuthenticationFormMixin
from app.discordauth.models import DiscordDevice

from app.preferences.utils import get_user_preference
from .preferences import UserRequiresMFA

from .models import OrgPlayer


class UnifiedUserCreationForm(BaseUserCreationForm):
    class Meta:
        model = OrgPlayer
        fields = ("username",)
        field_classes = {"username": UsernameField}


class UnifiedUserChangeForm(UserChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
#        if self.instance.pk is not None:
#            self.fields['username'].initial = self.instance.username

    class Meta:
        model = OrgPlayer
        fields = "__all__"
        field_classes = {"username": UsernameField}


class OptionalOTPAuthenticationFormMixin(OTPAuthenticationFormMixin):
    def clean_otp(self, user):
        """
        Processes the ``otp_*`` fields.

        :param user: A user that has been authenticated by the first factor
            (such as a password).
        :type user: :class:`~django.contrib.auth.models.User`
        :raises: :exc:`~django.core.exceptions.ValidationError` if the user is
            not fully authenticated by an OTP token.
        """
        if user is None:
            return

        mfa_required = get_user_preference(UserRequiresMFA.import_path, user)

        if not self.cleaned_data.get('otp_device') and not mfa_required:  # we allow a no MFA option
            user.otp_device = None
            return

        device = self._chosen_device(user)

        # this part is modified from original class
        if isinstance(device, DiscordDevice):
            token = {"user": user, "kind": "DiscordDevice"}
        else:
            token = self.cleaned_data.get('otp_token')

        user.otp_device = None

        try:
            if self.cleaned_data.get('otp_challenge'):
                self._handle_challenge(device)
            elif token:
                user.otp_device = self._verify_token(user, token, device)
            else:
                raise forms.ValidationError(self.otp_error_messages['token_required'], code='token_required')
        finally:
            if user.otp_device is None:
                self._update_form(user)

    def _update_form(self, user):
        super()._update_form(user)

        mfa_required = get_user_preference(UserRequiresMFA.import_path, user)

        if 'otp_device' in self.fields and not mfa_required:
            self.fields['otp_device'].widget.choices.append(["", "No MFA"])


class OptionalOTPAuthenticationForm(OptionalOTPAuthenticationFormMixin, AuthenticationForm):
    otp_device = forms.CharField(required=False, widget=forms.Select)
    otp_token = forms.CharField(required=False, widget=forms.TextInput(attrs={'autocomplete': 'off'}))

    # This is a placeholder field that allows us to detect when the user clicks
    # the otp_challenge submit button.
    otp_challenge = forms.CharField(required=False)

    def clean(self):
        self.cleaned_data = super().clean()
        self.clean_otp(self.get_user())

        return self.cleaned_data
