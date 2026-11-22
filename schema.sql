-- Run once in the Supabase SQL editor.
create table profiles(id uuid primary key references auth.users on delete cascade, credits int not null default 1);
create table runs(id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users on delete cascade,
  name text not null, status text not null default 'running', events jsonb not null default '[]', final text,
  created_at timestamptz not null default now());
create table payments(session_id text primary key, user_id uuid not null, credits int not null, created_at timestamptz default now());
alter table profiles enable row level security;
alter table runs enable row level security;
alter table payments enable row level security;
create policy "own profile" on profiles for select using (auth.uid() = id);
create policy "own runs" on runs for select using (auth.uid() = user_id);

create function handle_new_user() returns trigger language plpgsql security definer set search_path = public as $$
begin insert into profiles(id) values (new.id); return new; end $$;
create trigger on_auth_user_created after insert on auth.users for each row execute function handle_new_user();

create function spend_credit(uid uuid) returns boolean language plpgsql security definer set search_path = public as $$
begin update profiles set credits = credits - 1 where id = uid and credits > 0; return found; end $$;
create function add_credits(uid uuid, n int) returns void language plpgsql security definer set search_path = public as $$
begin update profiles set credits = credits + n where id = uid; end $$;
create function add_credits_once(sid text, uid uuid, n int) returns boolean language plpgsql security definer set search_path = public as $$
begin
  insert into payments(session_id, user_id, credits) values (sid, uid, n) on conflict do nothing;
  if not found then return false; end if;
  update profiles set credits = credits + n where id = uid;
  return true;
end $$;
revoke execute on function spend_credit(uuid), add_credits(uuid, int), add_credits_once(text, uuid, int) from public, anon, authenticated;
grant execute on function spend_credit(uuid), add_credits(uuid, int), add_credits_once(text, uuid, int) to service_role;
