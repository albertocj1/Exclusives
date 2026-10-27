-- Complimentary cocktails are tracked per guest (Entrance Fee and 6-Pax Bottle Bundle: 2 each).
-- Run once in Supabase BEFORE the backend update goes live: SQL Editor -> New query -> paste -> Run.
-- Safe to run more than once.

-- Which guest of the booking received the item (position in guest_names). Empty for table items.
alter table public.comp_claims add column if not exists guest_index int;

-- Slots are now counted per guest, so the "one item per slot" rule includes the guest.
drop index if exists public.comp_claims_active_slot;
create unique index if not exists comp_claims_active_slot_v2
  on public.comp_claims (booking_id, item_type, coalesce(guest_index, -1), slot)
  where voided_at is null;
