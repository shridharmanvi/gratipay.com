#!/usr/bin/env python
from __future__ import absolute_import, division, print_function, unicode_literals

import commands


def title(title):
    print(title)
    print("-"*34)

def report(*patterns):
    N = 0
    for pattern in patterns:
        n = int(commands.getoutput('grep "{}" backfill.log | wc -l'.format(pattern)))
        N += n
        print("{:<28} {:>5}".format(pattern, n))
    print("{:<28} {:>5}".format('', N))


report( 'IMPOSSIBLE!!!!!!!!!!!$'
      , 'all set!$'
      , 'yes$'
      , 'no$'
      , 'missing ref and status!$'
      , 'mismatched ref and status!$'
      , 'missing ref!$'
      , 'mismatched ref!$'
      , 'missing status!$'
      , 'mismatched status!$'
      , 'full of grace'
       )
