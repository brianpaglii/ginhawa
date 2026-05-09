// Dashboard data layer. Three queries — sessions, citizens,
// measurements — combined into one hook so the page can render
// per-section loading / error / data states cleanly.
//
// staleTime is 60 s and refetchOnWindowFocus is off: the dashboard
// is an aggregate view, not a live feed, and we don't want a
// chart to flicker when the user alt-tabs during a demo.

import { useQuery } from "@tanstack/react-query";

import {
  apiClient,
  type CitizenRead,
  type MeasurementRead,
  type Page,
  type SessionRead,
} from "../api/client";

const DASHBOARD_STALE_MS = 60_000;
const SESSIONS_PAGE_SIZE = 100;
const CITIZENS_PAGE_SIZE = 100;
const MEASUREMENTS_PAGE_SIZE = 100;

interface DashboardData {
  sessions: SessionRead[];
  citizens: CitizenRead[];
  measurements: MeasurementRead[];
}

interface DashboardQueryState {
  data: DashboardData;
  isPending: boolean;
  isError: boolean;
  error: unknown;
}

export function useDashboardStats(): DashboardQueryState {
  const sessionsQuery = useQuery<Page<SessionRead>>({
    queryKey: ["dashboard", "sessions"],
    queryFn: () => apiClient.listSessions({ limit: SESSIONS_PAGE_SIZE }),
    staleTime: DASHBOARD_STALE_MS,
    refetchOnWindowFocus: false,
  });

  const citizensQuery = useQuery<Page<CitizenRead>>({
    queryKey: ["dashboard", "citizens"],
    queryFn: () =>
      apiClient.listCitizens({ limit: CITIZENS_PAGE_SIZE, is_active: true }),
    staleTime: DASHBOARD_STALE_MS,
    refetchOnWindowFocus: false,
  });

  const measurementsQuery = useQuery<Page<MeasurementRead>>({
    queryKey: ["dashboard", "measurements"],
    queryFn: () =>
      apiClient.listMeasurements({ limit: MEASUREMENTS_PAGE_SIZE }),
    staleTime: DASHBOARD_STALE_MS,
    refetchOnWindowFocus: false,
  });

  return {
    data: {
      sessions: sessionsQuery.data?.items ?? [],
      citizens: citizensQuery.data?.items ?? [],
      measurements: measurementsQuery.data?.items ?? [],
    },
    isPending:
      sessionsQuery.isPending ||
      citizensQuery.isPending ||
      measurementsQuery.isPending,
    isError:
      sessionsQuery.isError ||
      citizensQuery.isError ||
      measurementsQuery.isError,
    error:
      sessionsQuery.error ?? citizensQuery.error ?? measurementsQuery.error,
  };
}
