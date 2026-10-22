"""Static diagnostic codes: no provider values, unknown field names or exception text."""


class OutputValidationError(ValueError):
    def __init__(self, category, field, rule):
        self.category, self.field, self.rule = category, field, rule
        super().__init__(rule)


class AnswerValidationError(OutputValidationError):
    pass


class SelectionValidationError(OutputValidationError):
    pass


def object_fields(value, fields, error_type):
    if not isinstance(value, dict):
        raise error_type('schema', '$', 'object_required')
    for field in fields:
        if field not in value:
            raise error_type('schema', field, 'required_field')
    if set(value) != set(fields):
        # Never expose arbitrary model-produced field names.
        raise error_type('schema', '$', 'unexpected_fields')
