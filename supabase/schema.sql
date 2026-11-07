-- Codwe AI: пользователи, роли и баны. Вставьте весь файл в Supabase -> SQL Editor -> New query -> Run.
-- Безопасно запускать повторно.

create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  role text not null default 'user' check (role in ('user', 'admin')),
  banned boolean not null default false,
  ban_reason text,
  tos_version int,
  tos_accepted_at timestamptz,
  created_at timestamptz not null default now(),
  last_seen timestamptz
);

alter table public.profiles enable row level security;

-- админ ли текущий пользователь (security definer: читает таблицу в обход RLS, поэтому нет рекурсии политик)
create or replace function public.is_admin()
returns boolean language sql security definer stable set search_path = public as $$
  select exists (select 1 from public.profiles where id = auth.uid() and role = 'admin' and not banned);
$$;

-- профиль создаётся автоматически при регистрации
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email) values (new.id, new.email) on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- политики: пользователь видит только себя, админ — всех. Прямых INSERT/UPDATE/DELETE из приложения нет,
-- все изменения идут через функции ниже.
drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own" on public.profiles for select to authenticated using (id = auth.uid());

drop policy if exists "profiles_select_admin" on public.profiles;
create policy "profiles_select_admin" on public.profiles for select to authenticated using (public.is_admin());

revoke all on public.profiles from anon, authenticated;
grant select on public.profiles to authenticated;

-- пользователь принял условия (версию v)
create or replace function public.accept_tos(v int)
returns void language sql security definer set search_path = public as $$
  update public.profiles set tos_version = v, tos_accepted_at = now() where id = auth.uid() and not banned;
$$;

-- «я здесь» (для списка пользователей)
create or replace function public.touch_seen()
returns void language sql security definer set search_path = public as $$
  update public.profiles set last_seen = now() where id = auth.uid();
$$;

-- админ: бан / разбан. Блокирует и вход в Supabase (banned_until), и профиль.
create or replace function public.admin_set_ban(target uuid, flag boolean, reason text default null)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'нет прав администратора'; end if;
  if target = auth.uid() then raise exception 'нельзя банить самого себя'; end if;
  update public.profiles set banned = flag, ban_reason = case when flag then reason else null end where id = target;
  update auth.users set banned_until = null where id = target;  -- вход не блокируем: человек видит экран бана с причиной
end;
$$;

-- админ: выдать / снять роль администратора
create or replace function public.admin_set_role(target uuid, new_role text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'нет прав администратора'; end if;
  if new_role not in ('user', 'admin') then raise exception 'неизвестная роль'; end if;
  if target = auth.uid() then raise exception 'нельзя менять свою роль'; end if;
  update public.profiles set role = new_role where id = target;
end;
$$;

revoke all on function public.accept_tos(int) from public, anon;
revoke all on function public.touch_seen() from public, anon;
revoke all on function public.admin_set_ban(uuid, boolean, text) from public, anon;
revoke all on function public.admin_set_role(uuid, text) from public, anon;
grant execute on function public.accept_tos(int) to authenticated;
grant execute on function public.touch_seen() to authenticated;
grant execute on function public.admin_set_ban(uuid, boolean, text) to authenticated;
grant execute on function public.admin_set_role(uuid, text) to authenticated;
grant execute on function public.is_admin() to authenticated;
