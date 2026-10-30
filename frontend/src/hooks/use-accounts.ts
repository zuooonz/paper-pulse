import { useQuery } from "@tanstack/react-query"
import { apiClient } from "@/lib/api"
import type { Account } from "@/lib/types"

export function useAccounts() {
  return useQuery<Account[]>({
    queryKey: ["accounts"],
    queryFn: async () => (await apiClient.get<Account[]>("/api/accounts")).data,
    staleTime: 60_000,
  })
}
