-- Codwe AI: РАНГИ (как роли в Discord), права, защита владельца, блокировка моделей, режим безопасности, журнал действий.
-- Запустите ОДИН раз: Supabase -> SQL Editor -> New query -> вставить весь файл -> Run. Можно запускать повторно.
-- Выполнять после schema.sql и access_password.sql (они у вас уже запущены).
--
-- Как работают ранги:
--   * У каждого ранга есть уровень (1..100). Чем выше число, тем выше ранг. Владелец = 100.
--   * Действовать можно только на тех, чей уровень СТРОГО НИЖЕ вашего (бан, смена ранга, сброс доступа).
--   * Выдать можно только ранг СТРОГО НИЖЕ вашего. Ранг «Владелец» выдать нельзя никому.
--   * Владельца нельзя забанить, понизить или изменить: база данных отклонит любое такое действие.
--   * Права (кто что может) хранятся в рангах. Менять ранги и их права может только владелец.
-- Все проверки выполняет сама база данных, а не приложение, поэтому обойти их через правку программы нельзя.

-- ───── 1. Таблица рангов ─────
create table if not exists public.ranks (
  key   text primary key check (key ~ '^[a-z][a-z0-9_]{1,23}$'),
  name  text not null check (char_length(name) between 1 and 24),
  level int  not null unique check (level between 1 and 100),
  color text not null default '#8d95aa' check (color ~ '^#[0-9a-fA-F]{6}$'),
  perms text[] not null default '{}'
);
alter table public.ranks enable row level security;
drop policy if exists "ranks_read" on public.ranks;
create policy "ranks_read" on public.ranks for select to authenticated using (true);
revoke all on public.ranks from anon, authenticated;
grant select on public.ranks to authenticated;

-- какие права вообще существуют
create or replace function public.valid_perms() returns text[]
language sql immutable as $$
  select array['users.view','users.ban','users.rank','access.manage','models.manage','safety.toggle','dev.console'];
$$;

insert into public.ranks (key, name, level, color, perms) values
  ('owner',     'Владелец',      100, '#f5b942', public.valid_perms()),
  ('admin',     'Администратор',  80, '#ef4444', array['users.view','users.ban','users.rank','access.manage','models.manage','dev.console']),
  ('moderator', 'Модератор',      50, '#3b82f6', array['users.view','users.ban']),
  ('trusted',   'Проверенный',    20, '#22c55e', '{}'),
  ('user',      'Пользователь',   10, '#8d95aa', '{}')
on conflict (key) do nothing;

-- ───── 2. Роль в профиле теперь ссылается на таблицу рангов ─────
alter table public.profiles drop constraint if exists profiles_role_check;
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'profiles_role_fkey') then
    alter table public.profiles add constraint profiles_role_fkey
      foreign key (role) references public.ranks (key) on update cascade;
  end if;
end $$;
-- владелец может быть только один
create unique index if not exists profiles_one_owner on public.profiles ((role)) where role = 'owner';

-- ───── 3. Вспомогательные функции ─────
create or replace function public.level_of(uid uuid) returns int
language sql security definer stable set search_path = public as $$
  select coalesce((select r.level from public.profiles p join public.ranks r on r.key = p.role where p.id = uid), 0);
$$;

create or replace function public.my_level() returns int
language sql security definer stable set search_path = public as $$
  select case when exists (select 1 from public.profiles where id = auth.uid() and banned) then 0
              else public.level_of(auth.uid()) end;
$$;

create or replace function public.has_perm(perm text) returns boolean
language sql security definer stable set search_path = public as $$
  select exists (select 1 from public.profiles p join public.ranks r on r.key = p.role
                 where p.id = auth.uid() and not p.banned and (r.key = 'owner' or perm = any (r.perms)));
$$;

-- старое имя оставлено: по нему работают политики чтения профилей (теперь «есть право видеть пользователей»)
create or replace function public.is_admin() returns boolean
language sql security definer stable set search_path = public as $$
  select public.has_perm('users.view');
$$;

-- ───── 4. Журнал действий (кто, что и кому сделал) ─────
create table if not exists public.audit_log (
  id bigserial primary key,
  at timestamptz not null default now(),
  actor uuid,
  actor_email text,
  action text not null,
  target text,
  detail text
);
alter table public.audit_log enable row level security;
revoke all on public.audit_log from anon, authenticated;

create or replace function public.log_action(act text, tgt text, det text default null) returns void
language sql security definer set search_path = public as $$
  insert into public.audit_log (actor, actor_email, action, target, detail)
  values (auth.uid(), (select email from public.profiles where id = auth.uid()), act, tgt, det);
$$;

create or replace function public.get_audit(n int default 60) returns setof public.audit_log
language plpgsql security definer set search_path = public as $$
begin
  if not public.has_perm('users.view') then raise exception 'нет права смотреть журнал'; end if;
  return query select * from public.audit_log order by id desc limit least(greatest(n, 1), 300);
end;
$$;

-- ───── 5. Бан и ранги (с иерархией) ─────
create or replace function public.admin_set_ban(target uuid, flag boolean, reason text default null)
returns void language plpgsql security definer set search_path = public as $$
declare tmail text;
begin
  if not public.has_perm('users.ban') then raise exception 'нет права блокировать пользователей'; end if;
  select email into tmail from public.profiles where id = target;
  if tmail is null then raise exception 'пользователь не найден'; end if;
  if target = auth.uid() then raise exception 'нельзя блокировать самого себя'; end if;
  if public.level_of(target) >= public.my_level() then
    raise exception 'нельзя: ранг этого пользователя не ниже вашего';
  end if;
  update public.profiles set banned = flag, ban_reason = case when flag then reason else null end where id = target;
  -- вход НЕ блокируем в облаке: забаненный заходит и видит экран «ВЫ БЫЛИ ЗАБАНЕНЫ» с причиной; работать ему не даёт профиль (banned)
  update auth.users set banned_until = null where id = target;
  perform public.log_action(case when flag then 'ban' else 'unban' end, tmail, reason);
end;
$$;

create or replace function public.admin_set_role(target uuid, new_role text)
returns void language plpgsql security definer set search_path = public as $$
declare tmail text; nl int; prev text;
begin
  if not public.has_perm('users.rank') then raise exception 'нет права менять ранги'; end if;
  select email, role into tmail, prev from public.profiles where id = target;
  if tmail is null then raise exception 'пользователь не найден'; end if;
  select level into nl from public.ranks where key = new_role;
  if nl is null then raise exception 'такого ранга нет'; end if;
  if new_role = 'owner' then raise exception 'ранг «Владелец» выдать нельзя'; end if;
  if target = auth.uid() then raise exception 'нельзя менять свой ранг'; end if;
  if public.level_of(target) >= public.my_level() then raise exception 'нельзя: ранг этого пользователя не ниже вашего'; end if;
  if nl >= public.my_level() then raise exception 'нельзя выдать ранг, который не ниже вашего'; end if;
  update public.profiles set role = new_role where id = target;
  perform public.log_action('rank', tmail, prev || ' -> ' || new_role);
end;
$$;

-- ───── 6. Пароль доступа (теперь по праву access.manage) ─────
create or replace function public.admin_set_access_password(pw text)
returns void language plpgsql security definer set search_path = public, extensions as $$
begin
  if not public.has_perm('access.manage') then raise exception 'нет права управлять доступом'; end if;
  if length(coalesce(pw, '')) < 8 then raise exception 'пароль слишком короткий (минимум 8 знаков)'; end if;
  insert into public.app_config (key, value) values ('access_hash', crypt(pw, gen_salt('bf', 10)))
    on conflict (key) do update set value = excluded.value;
  update public.profiles set access_ok = false, access_fails = 0
    where role not in (select key from public.ranks where key = 'owner' or 'access.manage' = any (perms));
  perform public.log_action('access-password-set', null, null);
end;
$$;

create or replace function public.admin_clear_access_password()
returns void language plpgsql security definer set search_path = public, extensions as $$
begin
  if not public.has_perm('access.manage') then raise exception 'нет права управлять доступом'; end if;
  delete from public.app_config where key = 'access_hash';
  perform public.log_action('access-password-clear', null, null);
end;
$$;

create or replace function public.admin_reset_access(target uuid)
returns void language plpgsql security definer set search_path = public as $$
declare tmail text;
begin
  if not public.has_perm('access.manage') then raise exception 'нет права управлять доступом'; end if;
  select email into tmail from public.profiles where id = target;
  if tmail is null then raise exception 'пользователь не найден'; end if;
  if target <> auth.uid() and public.level_of(target) >= public.my_level() then
    raise exception 'нельзя: ранг этого пользователя не ниже вашего';
  end if;
  update public.profiles set access_fails = 0, access_ok = false where id = target;
  perform public.log_action('access-reset', tmail, null);
end;
$$;

-- ───── 6b. Пароль доступа: защита от перебора через много аккаунтов ─────
-- Раньше у каждого аккаунта было 10 попыток. Теперь ещё и общий предел: не больше 120 неверных попыток на всех за 10 минут.
create table if not exists public.access_attempts (id bigserial primary key, uid uuid, ts timestamptz not null default now());
alter table public.access_attempts enable row level security;
revoke all on public.access_attempts from anon, authenticated;
create index if not exists access_attempts_ts on public.access_attempts (ts);

create or replace function public.check_access_password(pw text)
returns boolean language plpgsql security definer set search_path = public, extensions as $$
declare h text; ok boolean; p public.profiles;
begin
  if auth.uid() is null then return false; end if;
  select * into p from public.profiles where id = auth.uid();
  if p.id is null or p.banned then return false; end if;
  select value into h from public.app_config where key = 'access_hash';
  if h is null then
    update public.profiles set access_ok = true where id = auth.uid();
    return true;
  end if;
  if p.access_fails >= 10 then raise exception 'locked'; end if;
  if (select count(*) from public.access_attempts where ts > now() - interval '10 minutes') >= 120 then raise exception 'locked'; end if;
  if p.access_try is not null and now() - p.access_try < interval '3 seconds' and coalesce(pw, '') <> '' then
    raise exception 'too_fast';
  end if;
  ok := crypt(coalesce(pw, ''), h) = h;
  if ok then
    update public.profiles set access_ok = true, access_fails = 0, access_try = now() where id = auth.uid();
  elsif coalesce(pw, '') <> '' then
    update public.profiles set access_fails = access_fails + 1, access_try = now() where id = auth.uid();
    insert into public.access_attempts (uid) values (auth.uid());
    delete from public.access_attempts where ts < now() - interval '1 day';
  end if;
  return ok;
end;
$$;

-- ───── 7. Общие настройки: заблокированные модели и режим безопасности ─────
create table if not exists public.app_config (key text primary key, value text not null);
alter table public.app_config enable row level security;
revoke all on public.app_config from anon, authenticated;

-- читают все вошедшие (небаненные): приложение применяет это у каждого пользователя
create or replace function public.get_policy() returns json
language sql security definer stable set search_path = public as $$
  select json_build_object(
    'blocked_models', coalesce((select string_to_array(nullif(value, ''), ',') from public.app_config where key = 'blocked_models'), '{}'::text[]),
    'safety_soft', coalesce((select value from public.app_config where key = 'safety_soft'), 'on') <> 'off'
  )
  where auth.uid() is not null and exists (select 1 from public.profiles where id = auth.uid() and not banned);
$$;

create or replace function public.admin_set_model_block(model text, flag boolean) returns void
language plpgsql security definer set search_path = public as $$
declare cur text[];
begin
  if not public.has_perm('models.manage') then raise exception 'нет права управлять моделями'; end if;
  if model !~ '^[a-z]{3,8}:[A-Za-z0-9._:/\-]{1,100}$' then raise exception 'неверный номер модели'; end if;
  select coalesce(string_to_array(nullif(value, ''), ','), '{}'::text[]) into cur from public.app_config where key = 'blocked_models';
  cur := coalesce(cur, '{}'::text[]);
  cur := array_remove(cur, model);
  if flag then cur := array_append(cur, model); end if;
  insert into public.app_config (key, value) values ('blocked_models', array_to_string(cur, ','))
    on conflict (key) do update set value = excluded.value;
  perform public.log_action(case when flag then 'model-block' else 'model-unblock' end, model, null);
end;
$$;

create or replace function public.admin_set_safety(flag boolean) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.has_perm('safety.toggle') then raise exception 'нет права менять режим безопасности'; end if;
  insert into public.app_config (key, value) values ('safety_soft', case when flag then 'on' else 'off' end)
    on conflict (key) do update set value = excluded.value;
  perform public.log_action(case when flag then 'safety-on' else 'safety-off' end, null, null);
end;
$$;

-- ───── 8. Управление рангами (только владелец) ─────
create or replace function public.is_owner() returns boolean
language sql security definer stable set search_path = public as $$
  select exists (select 1 from public.profiles where id = auth.uid() and role = 'owner' and not banned);
$$;

create or replace function public.owner_set_rank(k text, n text, lvl int, col text, prm text[]) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.is_owner() then raise exception 'ранги меняет только владелец'; end if;
  if k = 'owner' then raise exception 'ранг владельца менять нельзя'; end if;
  if k !~ '^[a-z][a-z0-9_]{1,23}$' then raise exception 'ключ ранга: латинские буквы, цифры и _ (2–24 знака)'; end if;
  if lvl < 1 or lvl > 99 then raise exception 'уровень должен быть от 1 до 99'; end if;
  if col !~ '^#[0-9a-fA-F]{6}$' then raise exception 'цвет в формате #rrggbb'; end if;
  if exists (select 1 from unnest(coalesce(prm, '{}'::text[])) p where p <> all (public.valid_perms())) then
    raise exception 'неизвестное право в списке';
  end if;
  begin
    insert into public.ranks (key, name, level, color, perms) values (k, left(n, 24), lvl, col, coalesce(prm, '{}'))
      on conflict (key) do update set name = excluded.name, level = excluded.level, color = excluded.color, perms = excluded.perms;
  exception when unique_violation then
    raise exception 'этот уровень уже занят другим рангом';
  end;
  perform public.log_action('rank-save', k, 'level ' || lvl || ', perms: ' || array_to_string(coalesce(prm, '{}'), ','));
end;
$$;

create or replace function public.owner_delete_rank(k text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.is_owner() then raise exception 'ранги меняет только владелец'; end if;
  if k in ('owner', 'user') then raise exception 'этот ранг удалить нельзя'; end if;
  update public.profiles set role = 'user' where role = k;
  delete from public.ranks where key = k;
  perform public.log_action('rank-delete', k, null);
end;
$$;

-- ───── 9. Права на вызов функций ─────
revoke all on function public.level_of(uuid) from public, anon, authenticated;
revoke all on function public.log_action(text, text, text) from public, anon, authenticated;
revoke all on function public.get_audit(int) from public, anon;
revoke all on function public.my_level() from public, anon;
revoke all on function public.has_perm(text) from public, anon;
revoke all on function public.is_owner() from public, anon;
revoke all on function public.get_policy() from public, anon;
revoke all on function public.admin_set_ban(uuid, boolean, text) from public, anon;
revoke all on function public.admin_set_role(uuid, text) from public, anon;
revoke all on function public.admin_set_access_password(text) from public, anon;
revoke all on function public.admin_clear_access_password() from public, anon;
revoke all on function public.admin_reset_access(uuid) from public, anon;
revoke all on function public.admin_set_model_block(text, boolean) from public, anon;
revoke all on function public.admin_set_safety(boolean) from public, anon;
revoke all on function public.owner_set_rank(text, text, int, text, text[]) from public, anon;
revoke all on function public.owner_delete_rank(text) from public, anon;
grant execute on function public.get_audit(int) to authenticated;
grant execute on function public.my_level() to authenticated;
grant execute on function public.has_perm(text) to authenticated;
grant execute on function public.is_owner() to authenticated;
grant execute on function public.get_policy() to authenticated;
grant execute on function public.admin_set_ban(uuid, boolean, text) to authenticated;
grant execute on function public.admin_set_role(uuid, text) to authenticated;
grant execute on function public.admin_set_access_password(text) to authenticated;
grant execute on function public.admin_clear_access_password() to authenticated;
grant execute on function public.admin_reset_access(uuid) to authenticated;
grant execute on function public.admin_set_model_block(text, boolean) to authenticated;
grant execute on function public.admin_set_safety(boolean) to authenticated;
grant execute on function public.owner_set_rank(text, text, int, text, text[]) to authenticated;
grant execute on function public.owner_delete_rank(text) to authenticated;
grant execute on function public.is_admin() to authenticated;

-- ───── 9b. Сайт: кто скачал и общий счётчик регистраций ─────
alter table public.profiles add column if not exists downloaded_at timestamptz;

-- сайт вызывает это, когда вошедший пользователь нажимает «Скачать»
create or replace function public.mark_download() returns void
language sql security definer set search_path = public as $$
  update public.profiles set downloaded_at = coalesce(downloaded_at, now()) where id = auth.uid() and not banned;
$$;

-- публичный счётчик для главной страницы сайта: только число, без почт
create or replace function public.site_stats() returns json
language sql security definer stable set search_path = public as $$
  select json_build_object('users', (select count(*) from public.profiles));
$$;

revoke all on function public.mark_download() from public, anon;
grant execute on function public.mark_download() to authenticated;
revoke all on function public.site_stats() from public;
grant execute on function public.site_stats() to anon, authenticated;

-- ───── 10. ВЛАДЕЛЕЦ ─────
-- Почта владельца (профиль уже должен существовать, то есть вы входили в приложение с этой почтой).
update public.profiles set role = 'owner', access_ok = true, banned = false, ban_reason = null
 where lower(email) = 'ВАША_ПОЧТА@example.com'
   and not exists (select 1 from public.profiles where role = 'owner');
update auth.users set banned_until = null where lower(email) = 'ВАША_ПОЧТА@example.com';
-- старые блокировки входа снимаем: теперь бан показывается экраном, а не обрывом сессии
update auth.users set banned_until = null where id in (select id from public.profiles where banned);

-- Итог: внизу должна быть строка с вашей почтой и рангом owner.
select email, role from public.profiles order by created_at;
