#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# Copyright 2016-2018 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl

"""
It provides functions implementations to compile the Excel functions.

Sub-Modules:

.. currentmodule:: formulas.functions

.. autosummary::
    :nosignatures:
    :toctree: functions/

    ~eng
    ~financial
    ~info
    ~logic
    ~look
    ~math
    ~operators
    ~stat
    ~text
"""
import importlib
import functools
import collections
import numpy as np
import schedula as sh
import datetime
from formulas.errors import (
    RangeValueError, FunctionError, FoundError, BaseError, BroadcastError
)
from formulas.tokens.operand import Error, XlError


class Array(np.ndarray):
    _default = Error.errors['#N/A']

    _collapse_value = None

    def reshape(self, shape, *shapes, order='C'):
        try:
            return super(Array, self).reshape(shape, *shapes, order=order)
        except ValueError:
            res, (r, c) = np.empty(shape, object), self.shape
            res[:, :] = self._default
            r = None if r == 1 else r
            c = None if c == 1 else c
            try:
                res[:r, :c] = self
            except ValueError:
                res[:, :] = self.collapse(shape)
            return res

    def collapse(self, shape):
        if self._collapse_value is not None and tuple(shape) == (
        1, 1) != self.shape:
            return self._collapse_value
        return np.resize(self, shape)

    def __reduce__(self):
        reduce = super(Array, self).__reduce__() # Get the parent's __reduce__.
        state = {
            '_collapse_value': self._collapse_value,
            '_default': self._default
        },  # Additional state params to pass to __setstate__.
        return reduce[0], reduce[1], reduce[2] + state

    def __setstate__(self, state):
        self.__dict__.update(state[-1])  # Set the attributes.
        super(Array, self).__setstate__(state[0:-1])


# noinspection PyUnusedLocal
def not_implemented(*args, **kwargs):
    raise FunctionError()


def replace_empty(x, empty=0):
    if isinstance(x, np.ndarray):
        y = x.ravel().tolist()
        if sh.EMPTY in y:
            y = [empty if v is sh.EMPTY else v for v in y]
            return np.asarray(y, object).reshape(*x.shape)
    return x


# noinspection PyUnusedLocal
def wrap_func(func, ranges=False):
    def wrapper(*args, **kwargs):
        # noinspection PyBroadException
        try:
            return func(*args, **kwargs)
        except FoundError as ex:
            return np.asarray([[ex.err]], object)
        except BaseError as ex:
            raise ex
        except Exception:
            return np.asarray([[Error.errors['#VALUE!']]], object)

    if not ranges:
        return wrap_ranges_func(functools.update_wrapper(wrapper, func))
    return functools.update_wrapper(wrapper, func)


def wrap_ranges_func(func, n_out=1):
    def wrapper(*args, **kwargs):
        try:
            args, kwargs = parse_ranges(*args, **kwargs)
            return func(*args, **kwargs)
        except RangeValueError:
            return sh.bypass(*((sh.NONE,) * n_out))

    return functools.update_wrapper(wrapper, func)


def parse_ranges(*args, **kw):
    from ..ranges import Ranges
    args = tuple(v.value if isinstance(v, Ranges) else v for v in args)
    kw = {k: v.value if isinstance(v, Ranges) else v for k, v in kw.items()}
    return args, kw


SUBMODULES = [
    '.info', '.logic', '.math', '.stat', '.financial', '.text', '.look', '.eng', '.date'
]
# noinspection PyDictCreation
FUNCTIONS = {}
FUNCTIONS['ARRAY'] = lambda *args: np.asarray(args, object).view(Array)
FUNCTIONS['ARRAYROW'] = lambda *args: np.asarray(args, object).view(Array)


def get_error(*vals):
    # noinspection PyTypeChecker
    for v in flatten(vals, None):
        if isinstance(v, XlError):
            return v


def raise_errors(*args):
    # noinspection PyTypeChecker
    v = get_error(*args)
    if v:
        raise FoundError(err=v)


def is_number(number):
    if isinstance(number, bool):
        return False
    elif not isinstance(number, Error):
        try:
            float(number)
        except (ValueError, TypeError):
            return False
    return True

def excel_datevalue(d):
    if(isinstance(d, datetime.datetime)):
        return (d.date() - datetime.date(day=1, month=1, year=1900)).days + 2
    return (d - datetime.date(day=1, month=1, year=1900)).days + 2

def excel_filter(accumlator, test_range, condition, operating_range=None):

    from formulas.functions.operators import OPERATORS
    if(operating_range is None):
        operating_range = test_range

    ret = 0
    operating_r = list(flatten(operating_range, None))
    test_r = list(flatten(test_range, None))
    test = OPERATORS["="]
    try:
        # Check if starts with an operator
        operator = list(filter(lambda x: condition.strip().startswith(x), OPERATORS.keys()))[0]
        condition = float(condition.lstrip(operator))
        test = OPERATORS[operator]
    except (KeyError, IndexError, AttributeError):
        pass
    for i in range(0, len(test_r)):
        if(test(test_r[i], condition).item()):
            ret = accumlator(ret, operating_r[i])
    return ret

def convert_dates(l):
    if(isinstance(l, np.ndarray)):
        for x in np.nditer(l, op_flags=['readwrite'], flags=['refs_ok']):
            if(isinstance(x.item(), datetime.date)):
                x[...] = excel_datevalue(x.item())
    elif(isinstance(l, datetime.date)):
        return excel_datevalue(l)
    return l

def flatten(l, check=is_number):
    if not isinstance(l, str) and isinstance(l, collections.Iterable):
        try:
            for el in l:
                yield from flatten(el, check)
        except TypeError:
            yield from flatten(l.tolist(), check)
    elif not check or check(l):
        yield l


def value_return(res, *args):
    res._collapse_value = Error.errors['#VALUE!']
    return res


def wrap_ufunc(
        func, input_parser=lambda *a: map(float, a), check_error=get_error,
        args_parser=lambda *a: map(replace_empty, a), otype=Array,
        ranges=False, output_parser=lambda x: x, return_func=lambda res, *args: res, **kw):
    """Helps call a numpy universal function (ufunc)."""

    def safe_eval(*vals):
        try:
            r = check_error(*vals) or output_parser(func(*input_parser(*vals)))
            if not isinstance(r, (XlError, str)):
                try:
                    r = (np.isnan(r) or np.isinf(r)) and Error.errors['#NUM!'] or r
                except (TypeError, ) as e:
                    # Leave r unchanged. Most likely a date value that can't be cast to nan or inf
                    pass
        except (ValueError, TypeError) as e:
            r = Error.errors['#VALUE!']
        return r

    kw['otypes'] = kw.get('otypes', [object])

    # noinspection PyUnusedLocal
    def wrapper(*args, **kwargs):
        try:
            args = tuple(args_parser(*args))
            with np.errstate(divide='ignore', invalid='ignore'):
                res = np.vectorize(safe_eval, **kw)(*args)
            try:
                res = res.view(otype)
            except AttributeError:
                res = np.asarray([[res]], object).view(otype)
            return return_func(res, *args)
        except ValueError as ex:
            try:
                np.broadcast(*args)
            except ValueError:
                raise BroadcastError()
            raise ex

    return wrap_func(functools.update_wrapper(wrapper, func), ranges=ranges)


@functools.lru_cache()
def get_functions():
    functions = collections.defaultdict(lambda: not_implemented)
    for name in SUBMODULES:
        functions.update(importlib.import_module(name, __name__).FUNCTIONS)
    functions.update(FUNCTIONS)
    return functions
