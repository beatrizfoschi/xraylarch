#!/usr/bin/env python
"""utilities for larch
"""
from __future__ import print_function
import re
import sys

def PrintExceptErr(err_str, print_trace=True):
    " print error on exceptions"
    print('\n***********************************')
    print(err_str)
    #print 'PrintExceptErr', err_str
    try:
        print('Error: %s' % sys.exc_type)
        etype, evalue, tback = sys.exc_info()
        if print_trace == False:
            tback = ''
        sys.excepthook(etype, evalue, tback)
    except:
        print('Error printing exception error!!')
        raise
    print('***********************************\n')

def strip_comments(sinp, char='#'):
    "find character in a string, skipping over quoted text"
    if sinp.find(char) < 0:
        return sinp
    i = 0
    while i < len(sinp):
        tchar = sinp[i]
        if tchar in ('"',"'"):
            eoc = sinp[i+1:].find(tchar)
            if eoc > 0:
                i = i + eoc
        elif tchar == char:
            return sinp[:i].rstrip()
        i = i + 1
    return sinp


RESERVED_WORDS = ('and', 'as', 'assert', 'break', 'continue', 'def',
                  'del', 'elif', 'else', 'except', 'finally', 'for',
                  'from', 'if', 'import', 'in', 'is', 'not', 'or',
                  'pass', 'print', 'raise', 'return', 'try', 'while',
                  'group', 'end', 'endwhile', 'endif', 'endfor',
                  'endtry', 'enddef', 'True', 'False', 'None')


NAME_MATCH = re.compile(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*$").match
VALID_NAME_CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._'
def isValidName(name):
    "input is a valid name"
    tnam = name[:].lower()
    if tnam in RESERVED_WORDS:
        return False
    return NAME_MATCH(tnam) is not None

def fixName(name):
    "try to fix string to be a valid name"
    if isValidName(name):
        return name

    if isValidName('_%s' % name):
        return '_%s' % name
    chars = []
    for s in name:
        if s not in VALID_NAME_CHARS:
            s = '_'
        chars.append(s)
    name = ''.join(chars)
    # last check (name may begin with a number or .)
    if not isValidName(name):
        name = '_%s' % name
    return name

def isNumber(num):
    "input is a number"
    try:
        cnum = complex(num)
        return True
    except ValueError:
        return False

def isLiteralStr(inp):
    "is a literal string"
    return ((inp.startswith("'") and inp.endswith("'")) or
            (inp.startswith('"') and inp.endswith('"')))


