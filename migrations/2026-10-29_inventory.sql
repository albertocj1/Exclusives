-- Bar inventory: bottles and mixers on hand.
-- Run once in Supabase: SQL Editor -> New query -> paste -> Run. Safe to run more than once.

-- One row per count, restock, bottle opened for pouring, or adjustment. Stock on hand is
-- worked out from the latest count plus what happened since (see /api/inventory).
create table if not exists public.inventory_moves (
  id          uuid primary key default gen_random_uuid(),
  item        text not null,                       -- jager, absolut, jwblack, greygoose, evian, redbull, coke, sprite
  kind        text not null check (kind in ('count', 'restock', 'open', 'adjust')),
  qty         int  not null,                       -- count: number on hand; others: change
  note        text,
  by_email    text,
  created_at  timestamptz not null default now()
);
create index if not exists inventory_moves_item on public.inventory_moves (item, created_at);

-- Only the backend (secret key) reads or writes this table.
alter table public.inventory_moves enable row level security;

-- Starting stock. Only added when the table is empty, so running this again won't reset it.
insert into public.inventory_moves (item, kind, qty, note)
select v.item, 'count', v.qty, 'Starting stock'
from (values
  ('jager', 23), ('absolut', 12), ('jwblack', 16), ('greygoose', 3),
  ('evian', 56), ('redbull', 149), ('coke', 70), ('sprite', 51)
) as v(item, qty)
where not exists (select 1 from public.inventory_moves);
