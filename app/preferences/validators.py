
class SettingValidatorHelper(object):
    def __init__(self, validator, instance, user, admin_editing):
        self.validator = validator
        self.instance = instance
        self.user = user
        self.admin_editing = admin_editing

    def __call__(self, value):
        if value:
            return self.validator(value, self.instance, self.user, self.admin_editing)
        else:
            return value
