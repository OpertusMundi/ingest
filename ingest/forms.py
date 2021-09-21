from dataclasses import dataclass, field, fields

class ValidationError(Exception):
    pass

class CRSValidator:
    """Validates CRS fields."""
    def __init__(self, message=None):
        if not message:
            message = 'Field must be a valid CRS.'
        self.message = message

    def __call__(self, field):
        import pyproj
        from pyproj.exceptions import CRSError
        if field is None:
            return
        try:
            pyproj.crs.CRS.from_user_input(field)
        except CRSError:
            raise ValidationError(self.message)

class AnyOf:
    """Validates a field against a closed dictionary"""
    def __init__(self, values, message=None):
        self.values = values
        if not message:
            message = 'Field must be one of {}'.format(values)
        self.message = message

    def __call__(self, field):
        if field not in self.values:
            raise ValidationError(self.message)

class EncodingValidator:
    """Validates an encoding field."""
    def __init__(self, message=None):
        if not message:
            message = 'Field must be a valid encoding.'
        self.message = message

    def __call__(self, field):
        try:
            ''.encode(encoding=field, errors='replace')
        except LookupError:
            raise ValidationError(self.message)


class FileValidator:
    def __init__(self, message=None):
        if not message:
            message = 'Field must represent a path in the filesystem.'
        self.message = message

    def __call__(self, field):
        import os
        if field is None:
            return
        path = os.path.join(os.environ['INPUT_DIR'], field)
        if not os.path.isfile(path) and not os.path.isdir(path):
            raise ValidationError(self.message)

class Boolean:
    """Validates a field as a boolean."""
    def __init__(self, message=None):
        if not message:
            message = 'Field must be boolean.'
        self.message = message

    def __call__(self, field):
        import distutils.util
        if isinstance(field, bool):
            return
        try:
            bool_value = distutils.util.strtobool(field)
        except ValueError:
            raise ValidationError(self.message)

class Required:
    def __init__(self, message=None):
        if not message:
            message = 'Field is required.'
        self.message = message

    def __call__(self, field):
        if field is None:
            raise ValidationError(self.message)

@dataclass
class Form:
    def __post_init__(self):
        self._errors = {}

    @property
    def errors(self):
        return self._errors

    def validate(self):
        for f in fields(self):
            value = getattr(self, f.name)
            try:
                for validate in f.metadata['validate']:
                    validate(value)
            except KeyError:
                pass
            except ValidationError as e:
                try:
                    self._errors[f.name].append[str(e)]
                except KeyError:
                    self._errors[f.name] = [str(e)]
        return len(self._errors) == 0

@dataclass
class IngestForm(Form):
    resource: str = field(default=None, metadata={'validate': [FileValidator()]})
    response: str = field(default='prompt', metadata={'validate': [AnyOf(['prompt', 'deferred'])]})
    tablename: str = None
    schema: str = None
    replace: bool = field(default=False, metadata={'validate': [Boolean()]})
    encoding: str = field(default='utf-8', metadata={'validate': [EncodingValidator()]})
    crs: str = field(default=None, metadata={'validate': [CRSValidator()]})

@dataclass
class PublishForm(Form):
    table: str = field(default=None, metadata={'validate': [Required()]})
    schema: str = None
    workspace: str = None
