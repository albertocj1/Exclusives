-- Which named guests of a booking are inside (positions in guest_names), set by the reception tick boxes.
-- Run once in Supabase: SQL Editor -> New query -> paste -> Run. Safe to run more than once.
alter table public.bookings
  add column if not exists checked_in_guests jsonb not null default '[]'::jsonb;
