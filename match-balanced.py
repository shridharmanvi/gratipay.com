#!/usr/bin/env python2 -u
from __future__ import absolute_import, division, print_function, unicode_literals

import csv
import datetime
import os
import sys
from os import path

from gratipay import wireup


FUZZ = """\

        SELECT e.*, p.id as user_id
          FROM exchanges e
          JOIN participants p
            ON e.participant = p.username
         WHERE (
                (("timestamp" - %(created_at)s) >= '0 minutes')
                AND
                (("timestamp" - %(created_at)s) < '7 minutes')
               )
           AND (
                ((amount > 0) AND (amount + fee = %(amount)s))
                OR
                ((amount < 0) AND (amount = %(amount)s))
               )
           AND recorder IS NULL -- filter out PayPal

"""
FIND = FUZZ + """\

           AND participant = %(description)s

"""

HAIL_MARY = """\

        SELECT username AS participant
             , id AS user_id
          FROM participants
         WHERE username=%(description)s

"""


def find(log, db, rec):
    log("finding", rec['description'], end=' => ')
    return db.one(FIND, rec)


def fuzz(log, db, rec):
    log("fuzzing", rec['description'], end='')
    return db.all(FUZZ, rec)


def hail_mary(log, db, rec):
    log("full of grace", rec['description'])
    return db.one(HAIL_MARY, rec)


def process_month(db, cid2mat, uid2cid, year, month):
    input_csv = path.join('3912', year, month, '_balanced.csv')
    match_csv = path.join('3912', year, month, 'balanced')
    if not path.isfile(input_csv): return
    reader = csv.reader(open(input_csv))
    writer = csv.writer(open(match_csv, 'w+'))

    headers = next(reader)
    rec2mat = {}
    inexact = []
    ordered = []

    header = lambda h: print(h.upper() + ' ' + ((80 - len(h) - 1) * '-'))

    header("FINDING")
    for row in reader:
        rec = dict(zip(headers, row))
        #rec = dict({unicode(k):v for k,v in dict(rec).items()})

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

        match = find(log, db, rec)
        if match:
            uid = match.user_id
            known = uid2cid.get(uid)
            if known:
                assert cid == known, (rec, match)
            else:
                uid2cid[uid] = cid
                cid2mat[cid] = match
            rec2mat[rec['id']] = match

            if match.route is not None:
                assert match.ref == rec['id']
                assert match.status is not None
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
        guess = cid2mat.get(cid)

        fuzzed = fuzz(log, db, rec)
        keep = lambda m: (not m.user_id in uid2cid) or (guess and m.user_id == guess.user_id)
        possible = [m for m in fuzzed if keep(m)]
        npossible = len(possible)
        print(' => ', end='')

        match = None
        if npossible == 0:
            print('???', rec['amount'], end='')  # should log "skipping" below
        elif npossible == 1:
            match = possible[0]
            if cid in cid2mat:
                print('(again) ', end='')
            else:
                cid2mat[cid] = match
        elif guess:
            print('(guessing) ', end='')
            match = {m.participant:m for m in possible}.get(guess.participant)

        if match:
            print(match.participant)
        elif not possible:
            print(' ... IMPOSSIBLE!!!!!!!!!!!')
            continue
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

            possible.remove(match)
            print(match.participant, 'INSTEAD OF', ' OR '.join([p.participant for p in possible]))

        rec2mat[rec['id']] = match

    header("WRITING")
    for rec in ordered:
        match = rec2mat.get(rec['id'])
        if match is None:
            assert rec['status'] == 'failed', rec['id']
            match = cid2mat.get(cid)  # *any* successful exchanges for this user?
            if not match:
                match = hail_mary(log, db, rec)
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


def main(db, constraint):
    cid2mat = {}
    uid2cid = {}
    for year in os.listdir('3912'):
        if not year.isdigit(): continue
        for month in os.listdir('3912/' + year):
            if not month.isdigit(): continue
            if constraint and not '{}-{}'.format(year, month) == constraint: continue
            process_month(db, cid2mat, uid2cid, year, month)


if __name__ == '__main__':
    db = wireup.db(wireup.env())
    constraint = '' if len(sys.argv) < 2 else sys.argv[1]
    main(db, constraint)


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
