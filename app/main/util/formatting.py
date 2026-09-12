
class SafeDict(dict):
    """A dictionary that returns the key in braces if missing, preventing KeyError."""
    def __missing__(self, key):
        return '{' + key + '}'
