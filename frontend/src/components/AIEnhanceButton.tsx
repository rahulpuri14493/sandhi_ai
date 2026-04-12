// components/AIEnhanceButton.tsx
import React from "react";

interface AIEnhanceButtonProps {
  onClick: () => void;
  loading?: boolean;
  disabled?: boolean;
  /** `overlay` = corner of textarea; `inline` = flow layout (toolbar row, no overlap). */
  layout?: "overlay" | "inline";
}

/**
 * Sparkle/wand SVG icon button that triggers AI enhancement.
 */
export const AIEnhanceButton: React.FC<AIEnhanceButtonProps> = ({
  onClick,
  loading = false,
  disabled = false,
  layout = "overlay",
}) => {
  const isOverlay = layout === "overlay";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || loading}
      title="Enhance with AI"
      aria-label="Enhance job description with AI"
      style={{
        position: isOverlay ? "absolute" : "static",
        top: isOverlay ? "10px" : undefined,
        right: isOverlay ? "10px" : undefined,
        flexShrink: 0,
        background: loading ? "#e0e7ff" : "linear-gradient(135deg, #6366f1, #8b5cf6)",
        border: "none",
        borderRadius: "8px",
        padding: "6px 10px",
        cursor: disabled || loading ? "not-allowed" : "pointer",
        display: "flex",
        alignItems: "center",
        gap: "5px",
        fontSize: "12px",
        fontWeight: 600,
        color: loading ? "#6366f1" : "#fff",
        boxShadow: "0 2px 8px rgba(99,102,241,0.35)",
        transition: "opacity 0.2s, transform 0.15s",
        opacity: disabled ? 0.5 : 1,
        zIndex: isOverlay ? 10 : undefined,
      }}
      onMouseEnter={(e) => {
        if (!disabled && !loading) {
          (e.currentTarget as HTMLButtonElement).style.transform = "scale(1.05)";
        }
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.transform = "scale(1)";
      }}
    >
      {loading ? (
        <>
          <SpinnerIcon />
          <span>Enhancing…</span>
        </>
      ) : (
        <>
          <SparkleIcon />
          <span>Enhance with AI</span>
        </>
      )}
    </button>
  );
};

const SparkleIcon = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="14"
    height="14"
    viewBox="0 0 24 24"
    fill="currentColor"
    aria-hidden="true"
  >
    <path d="M12 2l2.09 6.26L20 10l-5.91 1.74L12 18l-2.09-6.26L4 10l5.91-1.74L12 2zm0 0" />
    <circle cx="19" cy="4" r="1.5" />
    <circle cx="5" cy="18" r="1" />
    <circle cx="19" cy="18" r="1.2" />
  </svg>
);

const SpinnerIcon = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="14"
    height="14"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.5"
    strokeLinecap="round"
    aria-hidden="true"
    style={{ animation: "spin 0.8s linear infinite" }}
  >
    <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    <path d="M12 2a10 10 0 0 1 10 10" />
  </svg>
);
