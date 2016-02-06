ALTER TYPE payment_net ADD VALUE 'samurai';
ALTER TYPE payment_net ADD VALUE 'stripe';

ALTER TABLE exchanges ADD UNIQUE (network, ref);
