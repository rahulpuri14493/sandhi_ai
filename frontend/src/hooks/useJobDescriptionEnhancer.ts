// hooks/useJobDescriptionEnhancer.ts
import { useState } from "react";

export interface EnhancedResult {
  corrected_text: string;
  recreated_prompts: string;
}

interface UseJobDescriptionEnhancerReturn {
  loading: boolean;
  error: string | null;
  result: EnhancedResult | null;
  enhance: (text: string) => Promise<void>;
  reset: () => void;
}

export function useJobDescriptionEnhancer(): UseJobDescriptionEnhancerReturn {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EnhancedResult | null>(null);

  const enhance = async (text: string) => {
    if (!text || text.trim().length === 0) {
      setError("Job description cannot be empty.");
      return;
    }
    if (text.trim().length < 10) {
      setError("Job description is too short to enhance.");
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const token = localStorage.getItem("access_token");
      const response = await fetch("/api/jobs/enhance-description-ai", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ description: text }),
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `Request failed (${response.status})`);
      }

      const data: EnhancedResult = await response.json();
      setResult(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  };

  const reset = () => {
    setResult(null);
    setError(null);
  };

  return { loading, error, result, enhance, reset };
}
