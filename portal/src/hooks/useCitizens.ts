// Citizen-related react-query hooks. Centralised here so the citizen
// list, citizen detail, and any future cross-cutting consumer share
// the same cache keys + stale-time policy.

import { useQuery } from "@tanstack/react-query";

import {
  apiClient,
  type CitizenRead,
  type Page,
  type SessionRead,
} from "../api/client";

// Citizens change rarely (registration is the only common mutation),
// so a 5-minute window keeps the list responsive without hammering
// the cloud as the user clicks around.
const CITIZENS_STALE_MS = 5 * 60_000;

// Sessions land continuously while a kiosk is in use; 30 s keeps the
// citizen-detail's session list close to live without spamming.
const CITIZEN_SESSIONS_STALE_MS = 30_000;

export function useCitizenList(
  page: number,
  size: number,
  options: { isActive?: boolean } = {},
) {
  return useQuery<Page<CitizenRead>>({
    queryKey: ["citizens", { page, size, isActive: options.isActive ?? true }],
    queryFn: () =>
      apiClient.listCitizens({
        limit: size,
        offset: page * size,
        is_active: options.isActive ?? true,
      }),
    staleTime: CITIZENS_STALE_MS,
    placeholderData: (previousData) => previousData,
  });
}

export function useCitizen(citizenId: string | undefined) {
  return useQuery<CitizenRead>({
    queryKey: ["citizen", citizenId],
    queryFn: () => apiClient.getCitizen(citizenId!),
    enabled: !!citizenId,
    staleTime: CITIZENS_STALE_MS,
  });
}

export function useCitizenSessions(
  citizenId: string | undefined,
  page: number,
  size: number,
) {
  return useQuery<Page<SessionRead>>({
    queryKey: ["sessions", { citizenId, page, size }],
    queryFn: () =>
      apiClient.listSessions({
        citizen_id: citizenId!,
        limit: size,
        offset: page * size,
      }),
    enabled: !!citizenId,
    staleTime: CITIZEN_SESSIONS_STALE_MS,
    placeholderData: (previousData) => previousData,
  });
}
