begin;

create table if not exists public.papers (
    id text primary key,
    title text not null,
    authors jsonb not null default '[]'::jsonb check (jsonb_typeof(authors) = 'array'),
    abstract text,
    venue text not null,
    year integer not null check (year between 1900 and 2200),
    doi text,
    url text,
    citation_count integer check (citation_count >= 0),
    topics jsonb not null default '[]'::jsonb check (jsonb_typeof(topics) = 'array'),
    source text not null default 'dblp',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists papers_venue_year_idx on public.papers (venue, year);

create table if not exists public.recommendations (
    id uuid primary key default gen_random_uuid(),
    paper_id text not null references public.papers(id),
    sent_at timestamptz,
    score double precision not null default 0,
    channel text not null,
    status text not null check (status in ('sent', 'failed')),
    created_at timestamptz not null default now(),
    check ((status = 'sent' and sent_at is not null)
        or (status = 'failed' and sent_at is null))
);

create index if not exists recommendations_sent_idx
    on public.recommendations (paper_id) where status = 'sent';

create or replace function public.set_paper_updated_at()
returns trigger language plpgsql set search_path = '' as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists papers_updated_at on public.papers;
create trigger papers_updated_at before update on public.papers
    for each row execute function public.set_paper_updated_at();

-- Only server-side secret/service_role credentials can access the bot's data.
alter table public.papers enable row level security;
alter table public.recommendations enable row level security;
revoke all on public.papers, public.recommendations from anon, authenticated;
grant select, insert, update, delete on public.papers, public.recommendations to service_role;

commit;
