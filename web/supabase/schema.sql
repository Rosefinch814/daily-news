create table if not exists sources (
  id text primary key,
  section_slug text not null,
  name text not null,
  url text not null,
  language text not null,
  type text not null default 'rss',
  enabled boolean not null default true,
  weight numeric not null default 1.0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists fetch_runs (
  id text primary key,
  section_slug text not null,
  issue_date date not null,
  status text not null,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists raw_items (
  id text primary key,
  fetch_run_id text references fetch_runs(id) on delete cascade,
  source_id text,
  source_name text not null,
  source_language text not null,
  title text not null,
  url text not null,
  published_at timestamptz,
  summary text,
  content text,
  fetch_status text not null,
  error text,
  fetched_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table if not exists candidates (
  id bigint generated always as identity primary key,
  fetch_run_id text references fetch_runs(id) on delete cascade,
  raw_item_id text references raw_items(id) on delete cascade,
  score numeric not null,
  matched_terms text[] not null default '{}',
  avoided_terms text[] not null default '{}',
  reason text not null,
  entered_ai boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists ai_runs (
  id bigint generated always as identity primary key,
  fetch_run_id text references fetch_runs(id) on delete cascade,
  task_type text not null,
  prompt_version text not null,
  prompt text not null,
  raw_output text not null,
  parsed_output jsonb,
  status text not null,
  error text,
  started_at timestamptz not null,
  finished_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table if not exists issues (
  id text primary key,
  fetch_run_id text references fetch_runs(id) on delete set null,
  section_slug text not null,
  publication_name text not null,
  issue_date date not null,
  volume integer not null,
  number integer not null,
  html_path text not null,
  status text not null,
  created_at timestamptz not null default now(),
  unique(section_slug, issue_date)
);

create table if not exists issue_articles (
  id bigint generated always as identity primary key,
  issue_id text references issues(id) on delete cascade,
  article_no integer not null,
  level text not null check (level in ('headline', 'brief')),
  title_zh text not null,
  summary_zh text not null,
  read_body_zh text[] not null default '{}',
  ai_impact text,
  sources jsonb not null,
  source_item_ids text[] not null default '{}',
  relevance_score integer not null,
  importance_score integer not null,
  created_at timestamptz not null default now()
);

create table if not exists feedback (
  id uuid primary key default gen_random_uuid(),
  issue_id text not null,
  issue_date date not null,
  section_slug text not null,
  scope text not null check (scope in ('article', 'issue')),
  article_level text check (article_level in ('headline', 'brief')),
  article_index integer check (article_index is null or article_index > 0),
  source_item_ids text[] not null default '{}',
  signal text check (signal in ('up', 'down')),
  note text check (note is null or char_length(note) <= 2000),
  created_at timestamptz not null default now(),
  digested_at timestamptz,
  check (cardinality(source_item_ids) <= 20),
  check (
    (scope = 'article' and article_level is not null and article_index is not null)
    or
    (scope = 'issue' and article_level is null and article_index is null)
  )
);

create index if not exists feedback_undigested_idx
  on feedback (section_slug, issue_date, created_at)
  where digested_at is null;

alter table sources enable row level security;
alter table fetch_runs enable row level security;
alter table raw_items enable row level security;
alter table candidates enable row level security;
alter table ai_runs enable row level security;
alter table issues enable row level security;
alter table issue_articles enable row level security;
alter table feedback enable row level security;

grant insert on feedback to anon;
grant select, insert, update on feedback to service_role;

drop policy if exists feedback_anon_insert on feedback;
create policy feedback_anon_insert on feedback
  for insert to anon
  with check (true);
