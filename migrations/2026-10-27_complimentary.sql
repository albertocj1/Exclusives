-- Complimentary bottles / food / cans.
-- Run once in Supabase: SQL Editor -> New query -> paste -> Run. Safe to run more than once.

-- Each booking's picks from the email replies (entered in admin).
alter table public.bookings
  add column if not exists comp_bottles jsonb not null default '[]'::jsonb,
  add column if not exists comp_food text;

-- One row per complimentary item handed out at the bar. Undo sets voided_at.
create table if not exists public.comp_claims (
  id          uuid primary key default gen_random_uuid(),
  claim_id    uuid not null,                       -- groups the rows of one "give" (e.g. 4 cans)
  booking_id  text not null,
  table_id    text,
  item_type   text not null check (item_type in ('bottle', 'food', 'can')),
  item_name   text,
  slot        int  not null check (slot >= 1),     -- 1..allowance; stops two bar devices giving the same item
  given_by    text,
  created_at  timestamptz not null default now(),
  voided_at   timestamptz
);
create unique index if not exists comp_claims_active_slot
  on public.comp_claims (booking_id, item_type, slot) where voided_at is null;
create index if not exists comp_claims_booking on public.comp_claims (booking_id);

-- Only the backend (secret key) reads or writes this table.
alter table public.comp_claims enable row level security;
