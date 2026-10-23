"use client";

import { useContext } from "react";

import { AuthContext } from "@/platform/components/rag/dashboard/AuthContext";

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
};
