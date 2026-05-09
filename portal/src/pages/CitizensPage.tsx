import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  ApiError,
  NetworkError,
  type CitizenRead,
  type Sex,
} from "../api/client";
import { CitizensEmptyIcon, EmptyState } from "../components/EmptyState";
import { Pagination } from "../components/Pagination";
import { SkeletonTable } from "../components/Skeleton";
import { useCitizenList } from "../hooks/useCitizens";
import { formatDateTime } from "../lib/datetime";
import { computeAge } from "../utils/age";
import styles from "./CitizensPage.module.css";

const PAGE_SIZE = 20;
const TABLE_COLUMNS = 6;

const SEX_LABELS: Record<Sex, string> = {
  M: "Male",
  F: "Female",
  O: "Other",
};

type SortKey = "full_name" | "registered_at";
type SortDir = "asc" | "desc";

interface SortState {
  key: SortKey;
  dir: SortDir;
}

// Default: server returns DESC by registered_at, so we mirror that as
// the default sort state so the toggle UI is consistent.
const DEFAULT_SORT: SortState = { key: "registered_at", dir: "desc" };

export function CitizensPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);

  const [searchInput, setSearchInput] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  // 250 ms debounce: enough to coalesce typing without making the
  // user wait between keystrokes. Filter is client-side, so this
  // debounce only governs how often we re-render.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchInput), 250);
    return () => clearTimeout(t);
  }, [searchInput]);

  const query = useCitizenList(page, PAGE_SIZE);

  const visibleRows = useMemo<CitizenRead[]>(() => {
    const all = query.data?.items ?? [];
    const term = debouncedSearch.trim().toLowerCase();
    const filtered = term
      ? all.filter((c) => c.full_name.toLowerCase().includes(term))
      : all;
    const sorted = [...filtered].sort((a, b) => {
      const cmp =
        sort.key === "full_name"
          ? a.full_name.localeCompare(b.full_name)
          : a.registered_at < b.registered_at
            ? -1
            : a.registered_at > b.registered_at
              ? 1
              : 0;
      return sort.dir === "asc" ? cmp : -cmp;
    });
    return sorted;
  }, [query.data?.items, debouncedSearch, sort]);

  function toggleSort(key: SortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "registered_at" ? "desc" : "asc" },
    );
  }

  if (query.isError) {
    return (
      <div role="alert" className={styles.error}>
        {messageFor(query.error)}
      </div>
    );
  }

  const isLoading = query.isPending;
  const data = query.data;

  return (
    <section>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Citizens</h1>
          <p className={styles.subtitle}>
            Active citizens registered in your barangay.
          </p>
        </div>
        <div className={styles.searchWrap}>
          <label htmlFor="citizens-search" style={{ display: "none" }}>
            Search citizens by name
          </label>
          <input
            id="citizens-search"
            type="search"
            className={styles.search}
            placeholder="Search by name…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.currentTarget.value)}
          />
        </div>
      </header>

      {isLoading ? (
        <SkeletonTable columns={TABLE_COLUMNS} rows={6} />
      ) : visibleRows.length === 0 ? (
        debouncedSearch ? (
          <div className={styles.empty}>
            No citizens matching “{debouncedSearch}”.
          </div>
        ) : (
          <EmptyState
            icon={CitizensEmptyIcon}
            title="No citizens registered"
            message="Citizens are added during their first kiosk visit, when they tap a new RFID card."
          />
        )
      ) : (
        <div className={styles.tableWrap}>
          <table className={`${styles.table} responsive-table`}>
            <thead>
              <tr>
                <SortableTh
                  label="Name"
                  active={sort.key === "full_name"}
                  dir={sort.dir}
                  onClick={() => toggleSort("full_name")}
                />
                <th scope="col">Age</th>
                <th scope="col">Sex</th>
                <th scope="col">Barangay</th>
                <SortableTh
                  label="Registered"
                  active={sort.key === "registered_at"}
                  dir={sort.dir}
                  onClick={() => toggleSort("registered_at")}
                />
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => navigate(`/citizens/${c.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      navigate(`/citizens/${c.id}`);
                    }
                  }}
                  tabIndex={0}
                  aria-label={`Open citizen ${c.full_name}`}
                >
                  <td data-label="Name">{c.full_name}</td>
                  <td data-label="Age">{computeAge(c.dob)} years</td>
                  <td data-label="Sex">{SEX_LABELS[c.sex] ?? c.sex}</td>
                  <td data-label="Barangay">{c.barangay}</td>
                  <td data-label="Registered">
                    {formatDateTime(c.registered_at)}
                  </td>
                  <td data-label="Status">
                    <span
                      className={`${styles.statusBadge} ${
                        c.is_active === 1
                          ? styles.statusActive
                          : styles.statusInactive
                      }`}
                    >
                      {c.is_active === 1 ? "Active" : "Inactive"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!isLoading && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={data!.total}
          shown={data!.items.length}
          busy={query.isFetching}
          onPageChange={setPage}
        />
      )}
    </section>
  );
}

function SortableTh({
  label,
  active,
  dir,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: SortDir;
  onClick: () => void;
}) {
  return (
    <th
      scope="col"
      className={styles.sortable}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      tabIndex={0}
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
    >
      {label}
      <span className={styles.sortIcon} aria-hidden>
        {active ? (dir === "asc" ? "▲" : "▼") : "↕"}
      </span>
    </th>
  );
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
  return "Something went wrong loading citizens.";
}
