#!/usr/bin/env python2 -u
from __future__ import absolute_import, division, print_function, unicode_literals

import csv
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
                ((("timestamp" - %(created_at)s) < '0 minutes') AND
                 (("timestamp" - %(created_at)s) > '-2 minutes'))
                 OR
                (("timestamp" - %(created_at)s) = '0 minutes')
                 OR
                ((("timestamp" - %(created_at)s) > '0 minutes') AND
                 (("timestamp" - %(created_at)s) < '2 minutes'))
               )
           AND amount + fee = %(amount)s
           AND amount > 0
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
    if not path.isfile(path.join('3912', year, month, '_balanced.csv')): return
    reader = csv.reader(open(path.join('3912', year, month, '_balanced.csv')))
    writer = csv.writer(open(path.join('3912', year, month, 'balanced.csv'), 'w+'))

    headers = next(reader)
    rec2mat = {}
    inexact = []
    ordered = []

    header = lambda h: print(h.upper() + ' ' + ((80 - len(h) - 1) * '-'))

    header("FINDING")
    for row in reader:
        rec = dict(zip(headers, row))
        rec = dict({unicode(k):v for k,v in dict(rec).items()})

        # convert cents to dollars
        rec['amount'] = '{}.{}'.format(rec['amount'][:-2], rec['amount'][-2:])
        if rec['amount'].startswith('.'):
            rec['amount'] = '0' + rec['amount']

        log = lambda *a, **kw: print(rec['created_at'], *a, **kw)

        ordered.append(rec)

        # translate status to our nomenclature
        if rec['status'] in ('succeeded', 'failed'):
            pass  # we'll deal with this next
        else:
            raise heck

        if rec['kind'] == 'card_hold':
            continue
        if rec['kind'] == 'credit':
            rec['amount'] = '-' + rec['amount']

        match = find(log, db, rec)
        if match and match.route is not None:
            assert match.ref == rec['id']
            assert match.status is not None
            assert match.route is not None
            ordered.pop()
            print('all set!')
        elif match:
            uid = match.user_id
            known = uid2cid.get(uid)
            if known:
                assert rec['links_customer'] == known, (rec, match)
            else:
                uid2cid[uid] = rec['links_customer']
                cid2mat[rec['links_customer']] = match
            rec2mat[rec['id']] = match
            print('yes')
        else:
            inexact.append(rec)
            print('no')

    header("FUZZING")
    for rec in inexact:
        guess = cid2mat.get(rec['links_customer'])

        fuzzed = fuzz(log, db, rec)
        possible = [m for m in fuzzed if not m.user_id in uid2cid]
        npossible = len(possible)
        print(' => ', end='')

        match = None
        if npossible == 0:
            print('???', rec['amount'], end='')  # should log "skipping" below
        elif npossible == 1:
            match = possible[0]
            if rec['links_customer'] in cid2mat:
                print('(again) ', end='')
            else:
                cid2mat[rec['links_customer']] = match
        elif guess:
            match = {m.participant:m for m in possible}.get(guess.participant)

        if match:
            print(match.participant)
            rec2mat[rec['id']] = match
        else:
            print(' OR '.join([p.participant for p in possible]))

    header("WRITING")
    for rec in ordered:
        match = rec2mat.get(rec['id'])
        if match is None:
            assert rec['status'] == 'failed'
            match = cid2mat.get(rec['links_customer'])  # *any* successful exchanges for this user?
            if not match:
                match = hail_mary(log, db, rec)
            writer.writerow([ match.participant
                            , match.user_id
                            , rec['Customer ID']
                            , ''
                            , rec['Created']
                            , rec['Amount']
                            , rec['id']
                            , rec['Status']
                             ])
        else:
            writer.writerow([ match.participant
                            , match.user_id
                            , rec['Customer ID']
                            , match.id
                            , ''
                            , ''
                            , rec['id']
                            , rec['Status']
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
