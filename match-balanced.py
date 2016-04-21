#!/usr/bin/env python2 -u
from __future__ import absolute_import, division, print_function, unicode_literals

import csv
import datetime
import os
import time
from collections import defaultdict
from decimal import Decimal as D
from os import path

import psycopg2.tz
from gratipay import wireup


header = lambda h: print(h.upper() + ' ' + ((80 - len(h) - 1) * '-'))


class Heck(Exception): pass


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


def usernames_match(transaction, exchange):
    if not exchange.participant: import pdb; pdb.set_trace()
    return transaction['description'] == exchange.participant


def amounts_match(transaction, exchange):
    amount = transaction['amount']
    if (exchange.amount > 0) and (exchange.amount + exchange.fee != amount):
        return False
    if (exchange.amount < 0) and (exchange.amount != amount):
        return False
    return True


def ts_within(transaction, exchange, seconds):
    ts_transaction = transaction['timestamp']
    ts_exchange = exchange.timestamp
    limit = ts_transaction + datetime.timedelta(seconds=seconds)
    return ts_exchange <= limit


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
    card2usernames = defaultdict(set)
    username2cids = defaultdict(set)

    for dirpath, dirnames, filenames in os.walk(root):
        for filename in filenames:
            if filename != '_balanced.csv':
                continue
            fp = open(os.path.join(dirpath, filename))
            reader = csv.reader(fp)
            headers = reader.next()

            for row in reader:
                rec = dict(zip(headers, row))
                username = rec['description']
                cid = rec['links__customer']

                # special-case the first test transactions
                if rec['id'] in ('WD7qFYL9rqIrCUmbXsgJJ8HT', 'WD16Zqy9ISWN5muEhXo19vpn'):
                    continue

                # special-case escrow shuffles to/from Gratipay
                if cid == 'AC13kr5rmbUkMJWbocmNs3tD':
                    continue

                # convert cents to decimal dollars
                rec['amount'] = '{}.{}'.format(rec['amount'][:-2], rec['amount'][-2:])
                if rec['amount'].startswith('.'):
                    rec['amount'] = '0' + rec['amount']
                rec['amount'] = D(rec['amount'])

                # convert created_at to timestamp
                rec['timestamp'] = datetime_from_iso(rec['created_at'])

                # check status
                if not rec['status'] in ('succeeded', 'failed'):
                    raise Exception(rec)

                # check kind
                if rec['kind'] == 'card_hold':
                    if rec['links__debit']:

                        # Balanced has one or two transactions, card_hold and
                        # possibly debit, where we only have one exchange. We
                        # can skip card_holds where there are debits.

                        continue
                    else:

                        # If we are gonna run with a card_hold, we need to jump
                        # through hoops to be able to deference a cid from it
                        # later on.

                        card = rec['links__card']
                        if not card or not username:
                            import pdb; pdb.set_trace()
                        card2usernames[card].add(username)

                elif rec['kind'] in ('credit', 'refund'):
                    rec['amount'] = -rec['amount']
                elif rec['kind'] in ('debit', 'reversal'):
                    pass
                else:
                    raise Exception(rec)

                # Map cid to usernames--more hoop-jumping
                if username and cid:
                    username2cids[username].add(cid)

                transactions.append(rec)

    # may not be necessary, but just to be sure ...
    transactions.sort(key=lambda rec: rec['created_at'])

    return transactions, card2usernames, username2cids


class Matcher(object):

    def __init__(self, db, root):
        print("Loading transactions ... ", end='')
        self.transactions, card2usernames, username2cids = get_transactions(root)
        print("we have {} transactions to match!".format(len(self.transactions)))

        print("Loading exchanges ... ", end='')
        self.exchanges = get_exchanges(db)
        print("we have {} exchanges to match!".format(len(self.exchanges)))

        self.uncategorized = {'transactions': [], 'exchanges': []}


        # Do goofiness to map cid to transactions

        card2cid = {}
        for t in self.transactions:
            cid, card = t['links__customer'], t['links__card']
            if cid == '':
                continue
            card2cid[card] = cid

        self.cid2transactions = defaultdict(list)
        for t in self.transactions:
            cid = t['links__customer']

            if t['status'] == 'failed' and t['created_at'] < '2014-12-18':
                # We didn't record failures before this date.
                self.uncategorized['transactions'].append(t)
                continue

            if not cid:
                if t['kind'] != 'card_hold' or t['links__debit'] != '':
                    self.uncategorized['transactions'].append(t)
                    continue
                usernames = card2usernames[t['links__card']]
                cids = set.union(*[username2cids[username] for username in usernames])
                if len(cids) != 1:
                    self.uncategorized['transactions'].append(t)
                    continue
                cid = tuple(cids)[0]

            if not cid:
                self.uncategorized['transactions'].append(t)
                continue

            self.cid2transactions[cid].append(t)

        # A little less goofiness to map uid to exchanges
        self.uid2exchanges = defaultdict(list)
        for e in self.exchanges:
            self.uid2exchanges[e.user_id].append(e)

        self.matches = []


    def inner_loop(self, cid, uid):

        transactions = self.cid2transactions[cid]
        exchanges = self.uid2exchanges[uid]


        # Remove from global lists
        # ========================
        # also decrement global indices if the transaction or exchange's
        # timestamp is less than the one that landed us here

        for transaction in transactions:
            if transaction['timestamp'] < self.transactions[self.I]['timestamp']:
                self.I -= 1
            self.transactions.remove(transaction)

        for exchange in exchanges:
            if exchange.timestamp < self.exchanges[self.J].timestamp:
                self.J -= 1
                self.K -= 1
            self.exchanges.remove(exchange)


        # Match items in the local lists if we can.
        # =========================================

        matched_t = set()
        matched_e = set()

        for t in transactions:
            if t['id'] in matched_t: continue
            for e in exchanges:
                if e.id in matched_e: continue
                if e.timestamp < t['timestamp']: continue
                if amounts_match(t, e) and ts_within(t, e, 6*3600):
                    matched_t.add(t['id'])
                    matched_e.add(e.id)
                    self.matches.append((t, e))
                    break

        self.uncategorized['transactions'] += [t for t in transactions if t['id'] not in matched_t]
        self.uncategorized['exchanges'] += [e for e in exchanges if e.id not in matched_e]


    def main(self):
        """Remove matches from _exchanges and _transactions and return a list of
        (exchange, transaction) match tuples
        """
        h = done = self.I = self.J = self.K = 0
        start = time.time()
        while not done:

            # output a progress report
            h += 1
            if h % 10 == 0:
                N = len(self.transactions)
                M = len(self.exchanges)
                perc = self.I / N
                remaining = int((time.time() - start) / (perc or 0.001))
                if remaining > 24*60*60:
                    remaining = '{:.1f} d'.format(remaining / 60 / 60 / 24)
                elif remaining > 60*60:
                    remaining = '{:.1f} h'.format(remaining / 60 / 60)
                elif remaining > 60:
                    remaining = '{:.1f} m'.format(remaining / 60)
                else:
                    remaining = '{} s'.format(remaining)
                print('\r{:>5} / {:>5} = {:4.1f}% | {:>5} / {:>5} = {:4.1f}% | {} matches | T-{}'
                      .format( self.I, N, perc * 100
                             , self.J, M, (self.J / M) * 100
                             , len(self.matches)
                             , remaining
                              ), end='')

            # Grab the next transaction and exchange.
            transaction = self.transactions[self.I]
            exchange = self.exchanges[self.J]

            # See if the two match.
            if amounts_match(transaction, exchange) and usernames_match(transaction, exchange):
                cid = transaction['links__customer']
                uid = exchange.user_id
                self.inner_loop(cid, uid)
                self.K = self.J
                continue

            # Advance the outer loop.
            done = self.advance()


    def advance(self):
        """Return bool (whether to continue the outer loop).
        """

        try:            # try incrementing J
            I = self.I
            J = self.J + 1

            # Check for the end of the list.
            if J > len(self.exchanges):
                raise Heck

            # Check for 10+ seconds beyond the transaction.
            if not ts_within(self.transactions[I], self.exchanges[J], 10):
                raise Heck

        except Heck:    # increment I instead

            I = self.I + 1
            J = self.K

            # Check for the end of the list.
            if I > len(self.transactions):
                return True

            # Reset J.
            transaction = self.transactions[I]
            while not ts_within(transaction, self.exchanges[J], 0):
                J -= 1

        self.I = I
        self.J = J
        return False


    def dump(self):
        out = csv.writer(open('balanced', 'w+'))
        for transaction, exchange in self.matches:
            out.writerow(( exchange.participant
                         , exchange.user_id
                         , transaction['links__customer']
                         , exchange.id
                         , exchange.amount
                         , transaction['id']
                         , transaction['status']
                          ))

        out = csv.writer(open('uncategorized.exchanges', 'w+'))
        for exchange in self.uncategorized['exchanges']:
            rec = [x[1] for x in exchange._asdict().items()]
            out.writerow(rec)

        out = csv.writer(open('uncategorized.transactions', 'w+'))
        for transaction in self.uncategorized['transactions']:
            rec = [x[1] for x in sorted(transaction.items())]
            out.writerow(rec)


if __name__ == '__main__':
    _db = wireup.db(wireup.env())
    _root = os.path.abspath('3912')
    matcher = Matcher(_db, _root)

    try:
        matcher.main()
    except KeyboardInterrupt:
        pass

    print("\nWe found {} matches!".format(len(matcher.matches)))
    matcher.dump()


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
