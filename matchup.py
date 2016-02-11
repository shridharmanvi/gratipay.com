#!/usr/bin/env python2
from __future__ import absolute_import, division, print_function, unicode_literals

import csv

from gratipay import wireup


def find(db, rec):
    exact = db.one("""

        SELECT e.*, p.id as user_id
          FROM exchanges e
          JOIN participants p
            ON e.participant = p.username
         WHERE "timestamp" - %(Created)s < '60 seconds'::interval
           AND amount + fee = %(Amount)s
           AND amount > 0
           AND participant = %(Description)s

    """, rec)
    if exact:
        out = [exact]
    else:
        out = db.all("""

            SELECT e.*, p.id as user_id
              FROM exchanges e
              JOIN participants p
                ON e.participant = p.username
             WHERE "timestamp" - %(Created)s < '60 seconds'::interval
               AND amount + fee = %(Amount)s
               AND amount > 0

        """, rec)
    return out


def main(db):
    reader = csv.reader(open('backfill/_stripe-transfers.csv'))
    writer = csv.writer(open('backfill/stripe', 'w'))
    headers = next(reader)
    for row in reader:
        rec = dict(zip(headers, row))
        matches = find(db, rec)
        for match in matches:
            writer.writerow([ match.participant
                            , match.user_id
                            , ''
                            , match.id
                            , rec['ID']
                            , 'succeeded'
                             ])
            if match.participant != rec['Description']:
                print(rec['Description'], '=>', match.participant)


if __name__ == '__main__':
    db = wireup.db(wireup.env())
    main(db)
