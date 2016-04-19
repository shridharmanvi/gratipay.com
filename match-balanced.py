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

STUBS = """\

        SELECT username AS participant
             , id AS user_id
          FROM participants

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


class Matcher(object):

    def __init__(self, db):
        self.db = db
        self.cid2uid = {}
        self.uid2cid = {}

    def load_month(self, year, month):
        self.exchanges = self.db.all(FULL, ('{}-{}'.format(year, month),))

    def load_stubs(self):
        self.username2stub = self.db.all(STUBS)

    def find(self, log, timestamp, amount, username):
        log("finding", username, end=' => ')
        found = self._find(log, timestamp, amount, uid=None, username=username)
        return found[0] if found else None

    def fuzz(self, log, timestamp, amount, uid, username):
        log("fuzzing", username, end='')
        fuzzed = self._find(log, timestamp, amount, uid=uid, username=username)
        fuzzed.sort(key=lambda x: x.id)
        return fuzzed

    def _find(self, log, timestamp, amount, uid, username):
        found = []
        i = 0
        timestamp = datetime_from_iso(timestamp)
        while i < len(self.exchanges):
            e = self.exchanges[i]
            i += 1

            # check uid
            if uid and e.user_id != uid:
                continue
            if uid is None and e.user_id in self.uid2cid:
                continue

            # check username
            if username and e.participant != username:
                continue

            # check amount
            amount = D(amount)
            if (e.amount > 0) and (e.amount + e.fee != amount):
                continue
            if (e.amount < 0) and (e.amount != amount):
                continue

            # check timestamp
            if e.timestamp < timestamp:
                # the Balanced record always precedes the db record
                continue

            # keep checking timestamp
            delta = e.timestamp - timestamp
            threshold = datetime.timedelta(minutes=7)
            if delta > threshold:
                break

            if not found:
                i -= 1
                self.exchanges.pop(i)
            found.append(e)

            if uid:
                break

        return found


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

        # special-case the first test transactions
        if rec['id'] in ('WD7qFYL9rqIrCUmbXsgJJ8HT', 'WD16Zqy9ISWN5muEhXo19vpn'):
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


def main(matcher, constraint):
    op = operator.eq
    if constraint and constraint[0] == '_':
        constraint = constraint[1:]
        op = operator.le

    for year in os.listdir('3912'):
        if not year.isdigit(): continue
        for month in os.listdir('3912/' + year):
            if not month.isdigit(): continue
            if constraint and not op('{}-{}'.format(year, month), constraint): continue
            process_month(matcher, year, month)


if __name__ == '__main__':
    _db = wireup.db(wireup.env())
    _matcher = Matcher(_db)
    _constraint = '' if len(sys.argv) < 2 else sys.argv[1]
    main(_matcher, _constraint)


"""
Fields in balanced.dat:

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
