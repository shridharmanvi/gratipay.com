#!/usr/bin/env python2
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import csv

from gratipay import wireup


def find(db, rec):
    return db.one("""

        SELECT e.*, p.id as user_id
          FROM exchanges e
          JOIN participants p
            ON e.participant = p.username
         WHERE "timestamp" - %(Created)s < '60 seconds'::interval
           AND amount + fee = %(Amount)s
           AND amount > 0
           AND participant = %(Description)s

    """, rec)


def fuzz(db, rec):
    return db.all("""

        SELECT e.*, p.id as user_id
          FROM exchanges e
          JOIN participants p
            ON e.participant = p.username
         WHERE "timestamp" - %(Created)s < '60 seconds'::interval
           AND amount + fee = %(Amount)s
           AND amount > 0

    """, rec)


def process_month(db, year, month):
    reader = csv.reader(open('3912/{}/{}/_stripe-payments.csv'.format(year, month)))
    writer = csv.writer(open('3912/{}/{}/stripe'.format(year, month), 'w+'))

    headers = next(reader)
    matched = []
    rec2mat = {}
    inexact = []
    ordered = []

    for row in reader:
        rec = dict(zip(headers, row))
        rec[b'Created'] = rec.pop('Created (UTC)')  # to make SQL interpolation easier

        ordered.append(rec)

        match = find(db, rec)
        if match:
            matched.append(match.user_id)
            rec2mat[rec['id']] = match
        else:
            inexact.append(rec)

    for rec in inexact:
        fuzzed = fuzz(db, rec)
        possible = [m for m in fuzzed if not m.user_id in matched]
        assert len(possible) == 1, possible
        guess = possible[0]
        print(rec['Description'], '=>', guess.participant)
        rec2mat[rec['id']] = guess

    for rec in ordered:
        match = rec2mat[rec['id']]
        writer.writerow([ match.participant
                        , match.user_id
                        , rec['Customer ID']
                        , match.id
                        , rec['id']
                        , rec['Status']
                         ])


def main(db):
    for year in os.listdir('3912'):
        if not year.isdigit(): continue
        for month in os.listdir('3912/' + year):
            if not month.isdigit(): continue
            process_month(db, year, month)


if __name__ == '__main__':
    db = wireup.db(wireup.env())
    main(db)


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
