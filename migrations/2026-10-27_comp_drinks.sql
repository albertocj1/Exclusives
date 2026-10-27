-- Allow complimentary cocktails (Entrance Fee: 2 per person) in comp_claims.
-- Run once in Supabase: SQL Editor -> New query -> paste -> Run. Safe to run more than once.
alter table public.comp_claims drop constraint if exists comp_claims_item_type_check;
alter table public.comp_claims
  add constraint comp_claims_item_type_check check (item_type in ('bottle', 'food', 'can', 'drink'));
