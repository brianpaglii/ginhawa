import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import {
  ApiError,
  apiClient,
  NetworkError,
  type CitizenRead,
  type ListSessionsParams,
  type MeasurementPath,
  type Page,
  type SessionRead,
  type SessionStatus,
} from "../api/client";
import { EmptyState, SessionsEmptyIcon } from "../components/EmptyState";
import { Pagination } from "../components/Pagination";
import { SkeletonTable } from "../components/Skeleton";
import { StatusPill } from "../components/StatusPill";
import { formatDateTime } from "../lib/datetime";
import styles from "./SessionsPage.module.css";

const PAGE_SIZE = 20;
const TABLE_COLUMNS = 5;
const CITIZENS_STALE_MS = 5 * 60_000;
const CITIZEN_SEARCH_DEBOUNCE_MS = 300;
const DEFAULT_RANGE_DAYS = 7;

const STATUSES: readonly SessionStatus[] = [
  "in_progress",
  "completed",
  "aborted",
  "error",
] as const;

const PATHS: readonly MeasurementPath[] = [
  "vitals",
  "anthropometric",
  "full",
] as const;

// URL-keys for filter state. Keep them short — they end up in user-
// shared links.
const PARAM_STATUS = "status";
const PARAM_PATH = "path";
const PARAM_FROM = "from";
const PARAM_TO = "to";
const PARAM_CITIZEN = "citizen";
const PARAM_SORT_DIR = "dir";
const PARAM_PAGE = "page";

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgoIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export function SessionsPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // ---- Filter state derived from URL ----------------------------
  // searchParams.get returns null for missing keys and "" for empty
  // ones. We use that distinction so "Clear filters" can fall back
  // to default-7-days while the user can still type-then-clear an
  // input to mean "no bound".
  const rawFrom = searchParams.get(PARAM_FROM);
  const rawTo = searchParams.get(PARAM_TO);
  const fromDate = rawFrom === null ? daysAgoIso(DEFAULT_RANGE_DAYS) : rawFrom;
  const toDate = rawTo === null ? todayIso() : rawTo;
  const status = (searchParams.get(PARAM_STATUS) ?? "") as SessionStatus | "";
  const pathFilter = (searchParams.get(PARAM_PATH) ?? "") as
    | MeasurementPath
    | "";
  const citizenId = searchParams.get(PARAM_CITIZEN) ?? "";
  const sortDir =
    (searchParams.get(PARAM_SORT_DIR) ?? "desc") === "asc" ? "asc" : "desc";
  const page = Number.parseInt(searchParams.get(PARAM_PAGE) ?? "0", 10) || 0;

  function patchParams(
    updates: Record<string, string | null>,
    options: { resetPage?: boolean } = {},
  ) {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(updates)) {
      if (value === null) next.delete(key);
      else next.set(key, value);
    }
    if (options.resetPage !== false) next.delete(PARAM_PAGE);
    setSearchParams(next);
  }

  function clearFilters() {
    setSearchParams(new URLSearchParams());
  }

  // ---- Query params hitting the cloud ---------------------------
  const queryParams = useMemo<ListSessionsParams>(() => {
    const params: ListSessionsParams = {
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      sort_dir: sortDir,
    };
    if (status) params.status = status;
    if (citizenId) params.citizen_id = citizenId;
    if (fromDate) params.started_after = `${fromDate}T00:00:00`;
    if (toDate) params.started_before = `${toDate}T23:59:59.999`;
    return params;
  }, [page, sortDir, status, citizenId, fromDate, toDate]);

  const sessionsQuery = useQuery<Page<SessionRead>>({
    queryKey: ["sessions", queryParams],
    queryFn: () => apiClient.listSessions(queryParams),
    placeholderData: (previousData) => previousData,
  });

  // ---- Citizens map (for row display + autocomplete suggestions)
  const citizensQuery = useQuery<Page<CitizenRead>>({
    queryKey: ["citizens"],
    queryFn: () => apiClient.listCitizens({ limit: 200, is_active: true }),
    staleTime: CITIZENS_STALE_MS,
  });
  // Memoise the unwrap so the [] fallback identity is stable across
  // renders — otherwise downstream useMemo dependencies (citizensById,
  // autocomplete matches) thrash on every render.
  const allCitizens = useMemo<CitizenRead[]>(
    () => citizensQuery.data?.items ?? [],
    [citizensQuery.data?.items],
  );
  const citizensById = useMemo(() => {
    const map = new Map<string, CitizenRead>();
    for (const c of allCitizens) map.set(c.id, c);
    return map;
  }, [allCitizens]);
  const selectedCitizen = citizenId ? citizensById.get(citizenId) : undefined;

  // ---- Path filter is client-side (cloud /sessions has no
  //      measurement_path query param). TODO: add server-side
  //      support so the filter doesn't only affect the visible
  //      page.
  const visibleItems = useMemo(() => {
    const items = sessionsQuery.data?.items ?? [];
    if (!pathFilter) return items;
    return items.filter((s) => s.measurement_path === pathFilter);
  }, [sessionsQuery.data?.items, pathFilter]);

  // ---- Status / path tweaks -------------------------------------
  function setStatus(value: string) {
    patchParams({ [PARAM_STATUS]: value === "" ? null : value });
  }
  function setPath(value: string) {
    patchParams({ [PARAM_PATH]: value === "" ? null : value });
  }
  function setFrom(value: string) {
    patchParams({ [PARAM_FROM]: value });
  }
  function setTo(value: string) {
    patchParams({ [PARAM_TO]: value });
  }
  function setCitizen(id: string | null) {
    patchParams({ [PARAM_CITIZEN]: id });
  }
  function toggleSortDir() {
    patchParams({ [PARAM_SORT_DIR]: sortDir === "desc" ? "asc" : "desc" });
  }
  function setPage(p: number) {
    patchParams(
      { [PARAM_PAGE]: p === 0 ? null : String(p) },
      { resetPage: false },
    );
  }

  if (sessionsQuery.isError) {
    return (
      <div role="alert" className={styles.error}>
        {messageFor(sessionsQuery.error)}
      </div>
    );
  }

  const isLoading = sessionsQuery.isPending;
  const data = sessionsQuery.data;
  const total = data?.total ?? 0;
  const showingFrom = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const showingTo = Math.min(total, page * PAGE_SIZE + visibleItems.length);

  const filtersDirty =
    rawFrom !== null ||
    rawTo !== null ||
    status !== "" ||
    pathFilter !== "" ||
    citizenId !== "" ||
    sortDir !== "desc";

  return (
    <section>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Sessions</h1>
          <p className={styles.subtitle}>
            Kiosk sessions in your barangay. Filter by status, path, date, or
            citizen.
          </p>
        </div>
      </header>

      <div className={styles.filters}>
        <div className={styles.filterField}>
          <label className={styles.filterLabel} htmlFor="filter-status">
            Status
          </label>
          <select
            id="filter-status"
            className={styles.filterSelect}
            value={status}
            onChange={(e) => setStatus(e.currentTarget.value)}
          >
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.filterField}>
          <label className={styles.filterLabel} htmlFor="filter-path">
            Path
          </label>
          <select
            id="filter-path"
            className={styles.filterSelect}
            value={pathFilter}
            onChange={(e) => setPath(e.currentTarget.value)}
          >
            <option value="">All</option>
            {PATHS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.filterField}>
          <label className={styles.filterLabel} htmlFor="filter-from">
            From
          </label>
          <input
            id="filter-from"
            type="date"
            className={styles.filterInput}
            value={fromDate}
            onChange={(e) => setFrom(e.currentTarget.value)}
          />
        </div>

        <div className={styles.filterField}>
          <label className={styles.filterLabel} htmlFor="filter-to">
            To
          </label>
          <input
            id="filter-to"
            type="date"
            className={styles.filterInput}
            value={toDate}
            onChange={(e) => setTo(e.currentTarget.value)}
          />
        </div>

        <CitizenAutocomplete
          allCitizens={allCitizens}
          selected={selectedCitizen}
          onSelect={(c) => setCitizen(c.id)}
          onClear={() => setCitizen(null)}
        />

        <div className={styles.filterActions}>
          <button
            type="button"
            className={styles.clearBtn}
            onClick={clearFilters}
            disabled={!filtersDirty}
          >
            Clear filters
          </button>
        </div>
      </div>

      {!isLoading && (
        <p className={styles.resultCount} aria-live="polite">
          Showing {showingFrom}–{showingTo} of {total} result
          {total === 1 ? "" : "s"}
        </p>
      )}

      {isLoading ? (
        <SkeletonTable columns={TABLE_COLUMNS} rows={6} />
      ) : visibleItems.length === 0 ? (
        filtersDirty ? (
          <EmptyState
            icon={SessionsEmptyIcon}
            title="No sessions match your filters"
            message="Try widening the date range, clearing the citizen, or hitting Clear filters above."
          />
        ) : (
          <EmptyState
            icon={SessionsEmptyIcon}
            title="No sessions yet"
            message="Sessions appear here as kiosks sync. Citizens scanning their RFID at a kiosk will create entries automatically."
          />
        )
      ) : (
        <div className={styles.tableWrap}>
          <table className={`${styles.table} responsive-table`}>
            <thead>
              <tr>
                <th
                  scope="col"
                  className={styles.sortable}
                  onClick={toggleSortDir}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      toggleSortDir();
                    }
                  }}
                  tabIndex={0}
                  aria-sort={sortDir === "asc" ? "ascending" : "descending"}
                >
                  Started
                  <span className={styles.sortIcon} aria-hidden>
                    {sortDir === "asc" ? "▲" : "▼"}
                  </span>
                </th>
                <th scope="col">Citizen</th>
                <th scope="col">Status</th>
                <th scope="col">Path</th>
                <th scope="col">Measurements</th>
              </tr>
            </thead>
            <tbody>
              {visibleItems.map((session) => {
                const citizen = citizensById.get(session.citizen_id);
                const citizenLabel =
                  citizen?.full_name ||
                  citizen?.rfid_uid ||
                  shortId(session.citizen_id);
                return (
                  <tr
                    key={session.id}
                    onClick={() => navigate(`/sessions/${session.id}`)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        navigate(`/sessions/${session.id}`);
                      }
                    }}
                    tabIndex={0}
                    aria-label={`Open session ${shortId(session.id)} for ${citizenLabel}`}
                  >
                    <td data-label="Started">
                      {formatDateTime(session.started_at)}
                    </td>
                    <td data-label="Citizen">{citizenLabel}</td>
                    <td data-label="Status">
                      <StatusPill status={session.status} />
                    </td>
                    <td data-label="Path">{session.measurement_path ?? "—"}</td>
                    <td data-label="Measurements">
                      {session.measurement_count}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {!isLoading && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={total}
          shown={visibleItems.length}
          busy={sessionsQuery.isFetching}
          onPageChange={setPage}
        />
      )}
    </section>
  );
}

interface AutocompleteProps {
  allCitizens: CitizenRead[];
  selected: CitizenRead | undefined;
  onSelect: (citizen: CitizenRead) => void;
  onClear: () => void;
}

function CitizenAutocomplete({
  allCitizens,
  selected,
  onSelect,
  onClear,
}: AutocompleteProps) {
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(
      () => setDebounced(search),
      CITIZEN_SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(t);
  }, [search]);

  // Click-away closer.
  useEffect(() => {
    function onClickAway(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener("mousedown", onClickAway);
    return () => document.removeEventListener("mousedown", onClickAway);
  }, [open]);

  const matches = useMemo<CitizenRead[]>(() => {
    const term = debounced.trim().toLowerCase();
    if (!term) return [];
    return allCitizens
      .filter(
        (c) =>
          c.full_name.toLowerCase().includes(term) ||
          c.rfid_uid.toLowerCase().includes(term),
      )
      .slice(0, 10);
  }, [debounced, allCitizens]);

  return (
    <div
      className={`${styles.filterField} ${styles.citizenComboWrap}`}
      ref={wrapRef}
    >
      <label className={styles.filterLabel} htmlFor="filter-citizen">
        Citizen
      </label>
      {selected ? (
        <div className={styles.citizenSelected}>
          <span>{selected.full_name}</span>
          <button
            type="button"
            className={styles.citizenSelectedClear}
            onClick={() => {
              onClear();
              setSearch("");
            }}
            aria-label={`Clear citizen filter ${selected.full_name}`}
          >
            ×
          </button>
        </div>
      ) : (
        <input
          id="filter-citizen"
          type="search"
          className={styles.filterInput}
          placeholder="Search by name or RFID…"
          value={search}
          onChange={(e) => {
            setSearch(e.currentTarget.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          autoComplete="off"
          // role="combobox" + aria-expanded would be ideal, but
          // would need full ARIA-spec wiring (active-descendant
          // etc.). For v1 we rely on the visible suggestions list
          // and click-to-select.
        />
      )}
      {open && matches.length > 0 && !selected && (
        <ul
          role="listbox"
          aria-label="Matching citizens"
          className={styles.citizenSuggestions}
        >
          {matches.map((c) => (
            <li
              key={c.id}
              role="option"
              aria-selected={false}
              tabIndex={0}
              className={styles.citizenSuggestion}
              onClick={() => {
                onSelect(c);
                setSearch("");
                setOpen(false);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(c);
                  setSearch("");
                  setOpen(false);
                }
              }}
            >
              <span className={styles.citizenSuggestionName}>
                {c.full_name}
              </span>
              <span className={styles.citizenSuggestionMeta}>
                {c.rfid_uid} · {c.barangay}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function shortId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401)
      return "Your session has expired. Please log in again.";
    return err.message || `Request failed (${err.status}).`;
  }
  if (err instanceof NetworkError) {
    return "Could not reach the server. Check your connection.";
  }
  return "Something went wrong loading sessions.";
}
