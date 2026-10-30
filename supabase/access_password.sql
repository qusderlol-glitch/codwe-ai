-- Codwe AI: пароль доступа (второй замок поверх входа по почте). Запустите один раз в SQL Editor. Можно повторно.
-- Сам пароль в базу открытым текстом не попадает: хранится только его хеш (bcrypt), а проверка идёт внутри базы.
-- Пароль задаёт администратор в админ-панели приложения (кнопка «Админ»), а не в этом файле.

create extension if not exists pgcrypto with schema extensions;

create table if not exists public.app_config (key text primary key, value text not null);
alter table public.app_config enable row level security;      -- политик нет: напрямую из приложения таблицу не прочитать
revoke all on public.app_config from anon, authenticated;

alter table public.profiles add column if not exists access_ok boolean not null default false;
alter table public.profiles add column if not exists access_fails int not null default 0;
alter table public.profiles add column if not exists access_try timestamptz;

-- проверка пароля доступа. Если пароль не задан, доступ открыт. Защита от перебора: пауза 3 с и блокировка после 10 ошибок.
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
  if p.access_try is not null and now() - p.access_try < interval '3 seconds' and coalesce(pw, '') <> '' then
    raise exception 'too_fast';
  end if;
  ok := crypt(coalesce(pw, ''), h) = h;
  if ok then
    update public.profiles set access_ok = true, access_fails = 0, access_try = now() where id = auth.uid();
  elsif coalesce(pw, '') <> '' then
    update public.profiles set access_fails = access_fails + 1, access_try = now() where id = auth.uid();
  end if;
  return ok;
end;
$$;

-- админ: задать пароль доступа (все остальные должны ввести его заново)
create or replace function public.admin_set_access_password(pw text)
returns void language plpgsql security definer set search_path = public, extensions as $$
begin
  if not public.is_admin() then raise exception 'нет прав администратора'; end if;
  if length(coalesce(pw, '')) < 8 then raise exception 'пароль слишком короткий (минимум 8 знаков)'; end if;
  insert into public.app_config (key, value) values ('access_hash', crypt(pw, gen_salt('bf', 10)))
    on conflict (key) do update set value = excluded.value;
  update public.profiles set access_ok = false, access_fails = 0 where role <> 'admin';
end;
$$;

-- админ: снять пароль доступа (вход открыт всем с аккаунтом)
create or replace function public.admin_clear_access_password()
returns void language plpgsql security definer set search_path = public, extensions as $$
begin
  if not public.is_admin() then raise exception 'нет прав администратора'; end if;
  delete from public.app_config where key = 'access_hash';
end;
$$;

-- админ: сбросить счётчик неверных попыток у пользователя
create or replace function public.admin_reset_access(target uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'нет прав администратора'; end if;
  update public.profiles set access_fails = 0, access_ok = false where id = target;
end;
$$;

revoke all on function public.check_access_password(text) from public, anon;
revoke all on function public.admin_set_access_password(text) from public, anon;
revoke all on function public.admin_clear_access_password() from public, anon;
revoke all on function public.admin_reset_access(uuid) from public, anon;
grant execute on function public.check_access_password(text) to authenticated;
grant execute on function public.admin_set_access_password(text) to authenticated;
grant execute on function public.admin_clear_access_password() to authenticated;
grant execute on function public.admin_reset_access(uuid) to authenticated;

-- сделать администратором вашу почту: ЗАМЕНИТЕ ВАША_ПОЧТА@example.com на свою (она должна уже войти в приложение хотя бы раз)
update public.profiles set role = 'admin', access_ok = true where email = 'ВАША_ПОЧТА@example.com';
