#!/usr/bin/env python2 -u
from __future__ import absolute_import, division, print_function, unicode_literals

import csv
import datetime
import operator
import os
import sys
from decimal import Decimal as D
from os import path

import psycopg2.tz
from gratipay import wireup


header = lambda h: print(h.upper() + ' ' + ((80 - len(h) - 1) * '-'))


FULL = """\

        SELECT e.*, p.id as user_id
          FROM exchanges e
          JOIN participants p
            ON e.participant = p.username
         WHERE substr("timestamp"::text, 0, 8) = %s
           AND recorder IS NULL -- filter out PayPal
      ORDER BY "timestamp" asc

"""


def datetime_from_iso(iso):
    date, time = iso.split('T')
    assert time[-1] == 'Z'
    time = time[:-1]
    year, month, day = map(int, date.split('-'))
    hour, minute, second_microsecond = time.split(':')
    hour, minute = map(int, (hour, minute))
    second, microsecond = map(int, second_microsecond.split('.'))
    tz = psycopg2.tz.FixedOffsetTimezone(offset=0, name=None)
    return datetime.datetime(year, month, day, hour, minute, second, microsecond, tzinfo=tz)


def process_month(matcher, year, month):
    input_csv = path.join('3912', year, month, '_balanced.csv')
    match_csv = path.join('3912', year, month, 'balanced')
    if not path.isfile(input_csv): return
    reader = csv.reader(open(input_csv))
    writer = csv.writer(open(match_csv, 'w+'))

    matcher.load_month(year, month)

    headers = next(reader)
    rec2mat = {}
    inexact = []
    ordered = []
    failed = set()

    header("FINDING")
    for row in reader:
        rec = dict(zip(headers, row))

        log = lambda *a, **kw: print(rec['created_at'], *a, **kw)

        cid = rec['links__customer']
        ordered.append(rec)

        match = matcher.find(log, rec['created_at'], rec['amount'], rec['description'])
        if match:
            uid = match.user_id
            known = matcher.uid2cid.get(uid)
            if known:
                assert cid == known, (rec, match)
            else:
                matcher.uid2cid[uid] = cid
                matcher.cid2uid[cid] = uid
            rec2mat[rec['id']] = match

            if match.route is not None:
                if match.ref is None and match.status is None:
                    print('missing ref and status!')
                elif match.ref != rec['id'] and match.status != rec['status']:
                    print('mismatched ref and status!')
                elif match.ref is None:
                    print('missing ref!')
                elif match.ref != rec['id']:
                    print('mismatched ref!')
                elif match.status is None:
                    print('missing status!')
                elif match.status != rec['status']:
                    print('mismatched status!')
                else:
                    ordered.pop()
                    print('all set!')
            else:
                print('yes')
        else:
            inexact.append(rec)
            print('no')

    header("FUZZING")
    for rec in inexact:
        cid = rec['links__customer']
        rid = rec['id']
        guess = matcher.cid2uid.get(cid)
        possible = matcher.fuzz(log, rec['created_at'], rec['amount'], guess, rec['description'])
        npossible = len(possible)

        def fail(msg):
            print(msg)
            failed.add(rid)

        print(' => ', end='')

        if guess:
            if npossible == 0:
                fail('Eep! Guess failed!')
            elif npossible > 1:
                fail('What?! Too many!')
            else:
                match = possible[0]
                print(match.participant)
        elif not possible:
            fail(' ... IMPOSSIBLE!!!!!!!!!!!')
        else:
            mindelta = None

            date, time = rec['created_at'].split('T')
            Y,M,D = date.split('-')
            h,m,s = time.split(':')
            s,ms = s.split('.')
            ms = ms[:-1]
            Y,M,D,h,m,s,ms = [int(x) for x in (Y,M,D,h,m,s,ms)]
            ts_balanced = datetime.datetime(Y,M,D,h,m,s,ms, possible[0].timestamp.tzinfo)

            for p in possible:
                delta = abs(ts_balanced - p.timestamp)
                if mindelta is None or (delta < mindelta):
                    mindelta = delta
                    match = p

            matcher.cid2uid[cid] = match.user_id
            possible.remove(match)
            print(match.participant, 'INSTEAD OF', ' OR '.join([p.participant for p in possible]))

        if rid in failed:
            continue

        rec2mat[rid] = match

    header("WRITING")
    for rec in ordered:
        if rec['id'] in failed: continue
        match = rec2mat.get(rec['id'])
        if match is None:
            assert rec['status'] == 'failed', rec['id']
            writer.writerow([ match.participant
                            , match.user_id
                            , rec['links__customer']
                            , ''
                            , rec['created_at']
                            , rec['amount']
                            , rec['id']
                            , rec['status']
                             ])
        else:
            writer.writerow([ match.participant
                            , match.user_id
                            , rec['links__customer']
                            , match.id
                            , ''
                            , ''
                            , rec['id']
                            , rec['status']
                             ])


### OLD ^^^^^^^^^^^^^^^^^^
### NEW vvvvvvvvvvvvvvvvvv


def get_exchanges(db):
    return db.all("""\

        SELECT e.*, p.id as user_id
          FROM exchanges e
          JOIN participants p
            ON e.participant = p.username
         WHERE recorder IS NULL -- filter out PayPal
      ORDER BY "timestamp" asc

    """)


def get_transactions(root):
    transactions = []
    for dirpath, dirnames, filenames in os.walk(root):
        for filename in filenames:
            if filename != '_balanced.csv':
                continue
            fp = open(os.path.join(dirpath, filename))
            reader = csv.reader(fp)
            headers = reader.next()
            for row in reader:
                rec = dict(zip(headers, row))

                # special-case the first test transactions
                if rec['id'] in ('WD7qFYL9rqIrCUmbXsgJJ8HT', 'WD16Zqy9ISWN5muEhXo19vpn'):
                    continue

                # special-case escrow shuffles to/from Gratipay
                if rec['links__customer'] == 'AC13kr5rmbUkMJWbocmNs3tD':
                    continue

                # convert cents to dollars
                rec['amount'] = '{}.{}'.format(rec['amount'][:-2], rec['amount'][-2:])
                if rec['amount'].startswith('.'):
                    rec['amount'] = '0' + rec['amount']

                # check status
                if not rec['status'] in ('succeeded', 'failed'):
                    raise Exception(rec)

                # check kind
                if rec['kind'] == 'card_hold':
                    continue    # we never tracked these in the Gratipay db
                elif rec['kind'] in ('credit', 'refund'):
                    rec['amount'] = '-' + rec['amount']
                elif rec['kind'] in ('debit', 'reversal'):
                    pass
                else:
                    raise Exception(rec)

                transactions.append(rec)

    # may not be necessary, but just to be sure ...
    transactions.sort(key=lambda rec: rec['created_at'])

    return transactions


class Matcher(object):

    def __init__(self, db, root):
        self.transactions = get_transactions(root)
        self.exchanges = get_exchanges(db)

        print("We have {} transactions to match!".format(len(self.transactions)))
        print("We have {} exchanges to match!".format(len(self.exchanges)))

        self.cid2uid = {}
        self.uid2cid = {}


    def main(self):
        passes = [self.first_pass]
        matches = []
        for pass_ in passes:
            matches.extend(pass_())
        print("We found {} matches!".format(len(matches)))


    def loop_over_exchanges(self, start, seconds):
        timestamp = datetime_from_iso(start)
        limit = timestamp + datetime.timedelta(seconds=seconds)
        for exchange in self.exchanges:
            if exchange.timestamp > limit:
                break
            yield exchange


    def first_pass(self):
        """Remove matches from _exchanges and _transactions and return a list of
        (exchange, transaction) match tuples
        """
        for i, transaction in enumerate(self.transactions):
            if i % 1000 == 0:
                print('.', end='')
            for exchange in self.loop_over_exchanges(transaction['created_at'], 10):
                continue
        return []


if __name__ == '__main__':
    _db = wireup.db(wireup.env())
    _root = os.path.abspath('3912')
    matcher = Matcher(_db, _root)
    matcher.main()


"""
Fields in balanced.csv:

    id
    kind
    meta_state
    meta_participant_id
    transaction_number
    status
    created_at
    updated_at
    failure_reason_code
    currency
    voided_at
    href
    amount
    description
    expires_at
    failure_reason
    meta_exchange_id
    appears_on_statement_as
    meta_balanced.result.trace_number
    meta_balanced.result.return_reason_code

"""
