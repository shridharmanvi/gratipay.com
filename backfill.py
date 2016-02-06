#!/usr/bin/env python2 -u
"""Script for backfilling exchange status, route, and ref.

Symlink a directory of data at `./backfill` and then call like so:

    [gratipay] $ run_dammit defaults.env local.env -c env/bin/python backfill.py

Data files should be one per network (named `samurai`, `stripe`, etc), as CSVs
with these columns:

    user_id         required    Gratipay participant.id
    username        ignored
    address         optional    defaults to 'fake-deadbeef'
    exchange_id     required    Gratipay exchanges.id
    status          optional    defaults to 'succeeded'
    ref             optional    defaults to 'fake-beeffeed'

For successfully backfilled exchanges (and routes), the script outputs the same
CSV as was input, with optional fields filled in. The script is idempotent (the
faked address and ref are hashed from other input values).

"""
from __future__ import absolute_import, division, print_function, unicode_literals

import csv
import os
import sha
import sys
from os import path

from gratipay import wireup
from gratipay.models.exchange_route import ExchangeRoute
from gratipay.models.participant import Participant


BASE = path.dirname(__file__)


def fake(*a):
    return 'fake-' + sha.new(''.join(map(str, a))).hexdigest()


def link(db, log, user_id, network, address, exchange_id, status, ref):
    participant = Participant.from_id(user_id)
    route = ExchangeRoute.from_network(participant, network)
    if route is None:
        route = ExchangeRoute.insert(participant, network, address)
    db.run( "UPDATE exchanges SET status=%s, route=%s, ref=%s WHERE id=%s"
          , (status, route.id, ref, exchange_id)
           )
    log(participant.id, participant.username, address, exchange_id, status, ref )


def main(db, log):
    for network in os.listdir('backfill'):
        data = csv.reader(open(path.join('backfill', network)))
        for user_id, _, address, exchange_id, status, ref in data:
            assert user_id
            address = address or fake(user_id, network)
            assert exchange_id
            status = status or 'succeeded'
            ref = ref or fake(user_id, network, exchange_id)

            link(db, log, user_id, network, address, exchange_id, status, ref)


if __name__ == '__main__':
    db = wireup.db(wireup.env())
    writer = csv.writer(sys.stdout)
    log = lambda *a: writer.writerow(a)
    main(db, log)
