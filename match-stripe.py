#!/usr/bin/env python2 -u
from __future__ import absolute_import, division, print_function, unicode_literals

import csv
import os
import sys

from gratipay import wireup


FUZZ = """

        SELECT e.*, p.id as user_id
          FROM exchanges e
          JOIN participants p
            ON e.participant = p.username
         WHERE (
                ((("timestamp" - %(Created)s) < '0 seconds') AND
                 (("timestamp" - %(Created)s) > '-61 seconds'))
                 OR
                (("timestamp" - %(Created)s) = '0 seconds')
                 OR
                ((("timestamp" - %(Created)s) > '0 seconds') AND
                 (("timestamp" - %(Created)s) < '61 seconds'))
               )
           AND amount + fee = %(Amount)s
           AND amount > 0

"""
FIND = FUZZ + """

           AND participant = %(Description)s

"""


def find(log, db, rec):
    log("finding", rec['Description'])
    return db.one(FIND, rec)


def fuzz(log, db, rec):
    log("fuzzing", rec['Description'], end='')
    return db.all(FUZZ, rec)


def process_month(db, year, month):
    reader = csv.reader(open('3912/{}/{}/_stripe-payments.csv'.format(year, month)))
    writer = csv.writer(open('3912/{}/{}/stripe'.format(year, month), 'w+'))

    headers = next(reader)
    matched = []
    rec2mat = {}
    inexact = []
    ordered = []
    fuz2mat = {}

    header = lambda h: print(h.upper() + ' ' + ((80 - len(h) - 1) * '-'))
    log = lambda *a, **kw: print('{}-{}'.format(year, month), *a, **kw)

    header("FINDING")
    for row in reader:
        rec = dict(zip(headers, row))
        rec[b'Created'] = rec.pop('Created (UTC)')  # to make SQL interpolation easier

        if rec['id'] == 'ch_Pi3yBdmevsIr5q':
            continue  # special-case the first test transaction

        if rec['Status'] == 'Failed':
            print("{} is a FAILURE!!!!!!!".format(rec['Description']))
            continue  # right?

        ordered.append(rec)

        match = find(log, db, rec)
        if match:
            matched.append(match.user_id)
            rec2mat[rec['id']] = match
        else:
            inexact.append(rec)

    header("FUZZING")
    for rec in inexact:
        guess = fuz2mat.get(rec['Description'])

        fuzzed = fuzz(log, db, rec)
        possible = [m for m in fuzzed if not m.user_id in matched]
        npossible = len(possible)
        print(' => ', end='')

        match = None
        if npossible == 0:
            print('???', rec['Amount'], rec['Created'])  # should log "skipping" below
        elif npossible == 1:
            match = possible[0]
            if rec['Description'] in fuz2mat:
                print('(again) ', end='')
            else:
                fuz2mat[rec['Description']] = match
        else:
            match = {m.participant:m for m in possible}.get(guess.participant)
            if not match:
                print(' OR '.join([p.participant for p in possible]))

        if match:
            print(match.participant)
            rec2mat[rec['id']] = match

    header("WRITING")
    for rec in ordered:
        match = rec2mat.get(rec['id'])
        if match is None:
            log("skipping", rec['Description'])
        else:
            writer.writerow([ match.participant
                            , match.user_id
                            , rec['Customer ID']
                            , match.id
                            , rec['id']
                            , rec['Status']
                             ])


def main(db, constraint):
    for year in os.listdir('3912'):
        if not year.isdigit(): continue
        for month in os.listdir('3912/' + year):
            if not month.isdigit(): continue
            if constraint and not '{}-{}'.format(year, month) == constraint: continue
            process_month(db, year, month)


if __name__ == '__main__':
    db = wireup.db(wireup.env())
    constraint = '' if len(sys.argv) < 2 else sys.argv[1]
    main(db, constraint)


"""
Fields in _stripe-payments.csv:

    id
    Description
    Created (UTC)
    Amount
    Amount Refunded
    Currency
    Converted Amount
    Converted Amount Refunded
    Fee
    Tax
    Converted Currency
    Mode
    Status
    Statement Descriptor
    Customer ID
    Customer Description
    Customer Email
    Captured
    Card ID
    Card Last4
    Card Brand
    Card Funding
    Card Exp Month
    Card Exp Year
    Card Name
    Card Address Line1
    Card Address Line2
    Card Address City
    Card Address State
    Card Address Country
    Card Address Zip
    Card Issue Country
    Card Fingerprint
    Card CVC Status
    Card AVS Zip Status
    Card AVS Line1 Status
    Card Tokenization Method
    Disputed Amount
    Dispute Status
    Dispute Reason
    Dispute Date (UTC)
    Dispute Evidence Due (UTC)
    Invoice ID
    Payment Source Type
    Destination
    Transfer

"""
