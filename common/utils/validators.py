"""Validation utilities for messages.""""""Utilities module for bucket operations and validators."""
import json
from abc import abstractmethod
from typing import Union, List, Dict, Any, ClassVar, Optional, Callable

class ReadOnlyMeta(type):
    """Abstract Base for ReadOnly Classes.

    Prohibits the modification of class variables.

    Usage:
        class Xyz(metaclass=ReadOnlyMeta):
            ...
    """
    def __setattr__(cls, name, value):
        if name in cls.__dict__:
            raise AttributeError(
                f"Cannot modify constant '{name}' on "
                f"ReadOnly Class {cls.__qualname__}")
        super().__setattr__(name, value)


class ValidatorMeta(type):
    """Abstract Base for Validator Classes.

    * class must have `validate` method
    * `validate` method must be an instance method
    * class variables are READ ONLY

    Usage:
        class Xyz(metaclass=ValidatorMeta):
            ...
    """
    def __new__(mcs, name, bases, dct):
        # Enforce that the 'validate' method is in the class dictionary
        method_name = 'validate'
        if ('validate' not in dct or
                isinstance(dct["validate"], (classmethod, staticmethod))):
            raise TypeError(
                f"Can't instantiate abstract class {name}"
                f"without an implementation for abstract class method {method_name}"
            )

        # Call the superclass's __new__ method to create the class
        return super().__new__(mcs, name, bases, dct)

    def __setattr__(cls, name, value):
        if name in cls.__dict__:
            raise AttributeError(
                f"Cannot modify constant '{name}' on "
                f"ReadOnly Class {cls.__qualname__}")
        super().__setattr__(name, value)


class DictValidator(metaclass=ValidatorMeta):
    """Validates the provided dict payload against validation specification.

    - applies any additional formatting if specified
    """
    class SchemaViolation(Exception):
        """Indicates the violation of validation specification"""

    SCHEMEA_VIOLATION_EXEPTION: ClassVar[type[Exception]] = SchemaViolation
    ALLOWED_EXTRA_KEYS: ClassVar[bool] = False
    FORMATTERS: ClassVar[dict] = {}
    VALIDATION_SPECIFICATION: ClassVar[dict] = {}

    @abstractmethod
    def validate(self, json_payload: dict,
                 logger_func: Optional[Callable[..., None]] = None,
                 message_wrapper: Optional[Callable[[str], str]] = None
                 ) -> None:
        """Default validation logic."""

        def do_logging(log_msg: str, level: str = 'info') -> None:
            wrapped = message_wrapper(log_msg) if message_wrapper else log_msg
            if logger_func:
                logger_func(wrapped, level=level)
            else:
                print(f'{level.upper()}: ', wrapped)

        # schema validation and formatting
        for key, spec in self.VALIDATION_SPECIFICATION.items():
            is_required, expected_type, default_value = spec

            if key not in json_payload:
                if is_required:
                    log_msg = (f'{self.SCHEMEA_VIOLATION_EXEPTION.__name__}:'
                               f'`{key}` Key Required')
                    do_logging(log_msg, 'error')
                    raise self.SCHEMEA_VIOLATION_EXEPTION(log_msg)
                json_payload[key] = default_value

            value = json_payload[key]

            if not isinstance(value, expected_type):
                log_msg = (f'`{key=}` is expected of type {expected_type}, '
                           f'got {type(value)}')
                do_logging(log_msg, 'error')
                raise self.SCHEMEA_VIOLATION_EXEPTION(log_msg)

            if not self.ALLOWED_EXTRA_KEYS:
                extra_keys = (json_payload.keys() -
                              self.VALIDATION_SPECIFICATION.keys())
                if extra_keys:
                    log_msg = f'Extra Key : {extra_keys} Not Allowed'
                    do_logging(log_msg, 'error')
                    raise self.SCHEMEA_VIOLATION_EXEPTION(log_msg)

            json_payload[key] = self.FORMATTERS.get(key, lambda v: v)(value)
