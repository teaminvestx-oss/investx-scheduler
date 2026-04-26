-- Run this in your Supabase SQL editor to create the users table
create table users (
  telegram_id bigint primary key,
  username text,
  first_name text,
  tier text default 'free',
  queries_this_month integer default 0,
  queries_limit integer default 10,
  registered_at timestamp default now(),
  last_query_at timestamp,
  reset_date date default (date_trunc('month', now()) + interval '1 month')
);
