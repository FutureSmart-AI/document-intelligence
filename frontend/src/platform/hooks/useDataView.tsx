"use client";

import { useCallback, useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/platform/hooks/useAuth";
import { getDataViewTabular } from "@/platform/services/data-view";
import type {
  DataViewRequest,
  FilterValue,
  SortCriteria,
} from "@/platform/types/data-view";

export const useDataView = () => {
  const { knowledgeId } = useAuth();
  const [request, setRequest] = useState<DataViewRequest>({
    page_size: 10,
    page: 1,
    sort: [],
  });

  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: ["dataView", knowledgeId, request],
    queryFn: () => {
      if (!knowledgeId) {
        throw new Error("Knowledge ID is required");
      }
      return getDataViewTabular(knowledgeId, request);
    },
    enabled: Boolean(knowledgeId),
    staleTime: 30000, // 30 seconds
  });

  const updateFilters = useCallback((filters: Record<string, FilterValue>) => {
    setRequest((prev) => ({
      ...prev,
      filters,
      page: 1,
    }));
  }, []);

  const updateSort = useCallback((sort: SortCriteria[]) => {
    setRequest((prev) => ({
      ...prev,
      sort,
      page: 1,
    }));
  }, []);

  const updateColumns = useCallback((columns: string[]) => {
    setRequest((prev) => ({
      ...prev,
      columns,
      page: 1,
    }));
  }, []);

  const updatePageSize = useCallback((pageSize: number) => {
    setRequest((prev) => ({
      ...prev,
      page_size: pageSize,
      page: 1,
    }));
  }, []);

  const nextPage = useCallback(() => {
    if (!data || data.current_page >= data.total_pages) {
      return; // No next page available
    }

    setRequest((prev) => ({
      ...prev,
      page: (prev.page || 1) + 1,
    }));
  }, [data]);

  const previousPage = useCallback(() => {
    setRequest((prev) => {
      const currentPage = prev.page || 1;
      if (currentPage <= 1) return prev;
      return {
        ...prev,
        page: currentPage - 1,
      };
    });
  }, []);

  // Allow jumping to a specific page if needed
  const goToPage = useCallback((page: number) => {
    setRequest((prev) => ({
      ...prev,
      page,
    }));
  }, []);

  const resetFilters = useCallback(() => {
    setRequest({
      page_size: 10,
      page: 1,
      sort: [],
      columns: ["keywords"],
    });
  }, []);

  const canGoToPreviousPage = (request.page || 1) > 1;
  const canGoToNextPage = data ? data.current_page < data.total_pages : false;

  return {
    data,
    isLoading,
    isFetching,
    error,
    refetch,
    updateFilters,
    updateSort,
    updateColumns,
    updatePageSize,
    nextPage,
    previousPage,
    goToPage,
    resetFilters,
    request,
    canGoToPreviousPage,
    canGoToNextPage,
  };
};
