// components/JobDescriptionEnhanceModal.tsx
import React, { useEffect, useRef } from "react";
import { EnhancedResult } from "../hooks/useJobDescriptionEnhancer";

interface JobDescriptionEnhanceModalProps {
  isOpen: boolean;
  original: string;
  result: EnhancedResult | null;
  error: string | null;
  loading: boolean;
  onApply: (text: string) => void;
  onCancel: () => void;
}

/**
 * Side-by-side modal: left = original, right = AI-enhanced preview.
 * User chooses "Apply" (corrected_text) or "Cancel" to discard.
 */
export const JobDescriptionEnhanceModal: React.FC<
  JobDescriptionEnhanceModalProps
> = ({ isOpen, original, result, error, loading, onApply, onCancel }) => {
  const dialogRef = useRef<HTMLDivElement>(null);

  // Trap focus inside modal & close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", handleKey);
    dialogRef.current?.focus();
    return () => window.removeEventListener("keydown", handleKey);
  }, [isOpen, onCancel]);

  if (!isOpen) return null;

  const enhanced = result
    ? `${result.corrected_text}\n\n---\n\n📋 Refined Prompts:\n${result.recreated_prompts}`
    : "";

  return (
    // Overlay
    <div
      role="dialog"
      aria-modal="true"
      aria-label="AI Enhancement Preview"
      style={overlayStyle}
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      {/* Modal box */}
      <div ref={dialogRef} tabIndex={-1} style={modalStyle}>
        {/* Header */}
        <div style={headerStyle}>
          <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={badgeStyle}>✨ AI Enhancement Preview</span>
          </span>
          <button
            onClick={onCancel}
            style={closeButtonStyle}
            aria-label="Close modal"
            title="Close"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div style={bodyStyle}>
          {/* LEFT — original */}
          <div style={panelStyle}>
            <div style={panelHeaderStyle("#6b7280")}>
              <span>📄 Original</span>
            </div>
            <textarea
              readOnly
              value={original}
              style={textareaStyle}
              aria-label="Original job description"
            />
          </div>

          {/* Divider */}
          <div style={dividerStyle}>
            <span style={arrowStyle}>→</span>
          </div>

          {/* RIGHT — enhanced */}
          <div style={panelStyle}>
            <div style={panelHeaderStyle("#6366f1")}>
              <span>✨ AI Enhanced</span>
            </div>
            {loading && (
              <div style={stateStyle}>
                <Spinner />
                <p style={{ color: "#6366f1", marginTop: "12px" }}>
                  Processing your description…
                </p>
              </div>
            )}
            {error && !loading && (
              <div style={{ ...stateStyle, color: "#dc2626" }}>
                <p>⚠️ {error}</p>
              </div>
            )}
            {result && !loading && (
              <textarea
                readOnly
                value={enhanced}
                style={{ ...textareaStyle, borderColor: "#a5b4fc" }}
                aria-label="AI enhanced job description"
              />
            )}
          </div>
        </div>

        {/* Footer */}
        <div style={footerStyle}>
          <button
            onClick={onCancel}
            style={cancelBtnStyle}
            aria-label="Discard AI suggestions"
          >
            Cancel
          </button>
          <button
            onClick={() => result && onApply(result.corrected_text)}
            disabled={!result || loading}
            style={applyBtnStyle(!result || loading)}
            aria-label="Apply AI enhanced description"
          >
            Apply Enhancement
          </button>
        </div>
      </div>
    </div>
  );
};

// ─── Styles ────────────────────────────────────────────────────────────────

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(15, 23, 42, 0.6)",
  backdropFilter: "blur(4px)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 1000,
  padding: "16px",
};

const modalStyle: React.CSSProperties = {
  background: "#fff",
  borderRadius: "16px",
  width: "100%",
  maxWidth: "900px",
  maxHeight: "90vh",
  display: "flex",
  flexDirection: "column",
  boxShadow: "0 25px 60px rgba(0,0,0,0.25)",
  outline: "none",
  overflow: "hidden",
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "16px 20px",
  borderBottom: "1px solid #e5e7eb",
  background: "linear-gradient(135deg, #f5f3ff, #ede9fe)",
};

const badgeStyle: React.CSSProperties = {
  fontWeight: 700,
  fontSize: "15px",
  color: "#4f46e5",
};

const closeButtonStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  fontSize: "18px",
  cursor: "pointer",
  color: "#6b7280",
  lineHeight: 1,
  padding: "2px 6px",
  borderRadius: "6px",
};

const bodyStyle: React.CSSProperties = {
  display: "flex",
  flex: 1,
  gap: "0",
  overflow: "hidden",
  minHeight: "300px",
};

const panelStyle: React.CSSProperties = {
  flex: 1,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

const panelHeaderStyle = (color: string): React.CSSProperties => ({
  padding: "10px 16px",
  fontWeight: 600,
  fontSize: "13px",
  color,
  background: "#f9fafb",
  borderBottom: `2px solid ${color}22`,
  display: "flex",
  alignItems: "center",
  gap: "6px",
});

const textareaStyle: React.CSSProperties = {
  flex: 1,
  resize: "none",
  border: "none",
  outline: "none",
  padding: "16px",
  fontSize: "14px",
  lineHeight: "1.6",
  fontFamily: "inherit",
  color: "#1f2937",
  background: "#fff",
  height: "100%",
  minHeight: "280px",
};

const dividerStyle: React.CSSProperties = {
  width: "40px",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "#f3f4f6",
  flexShrink: 0,
  borderLeft: "1px solid #e5e7eb",
  borderRight: "1px solid #e5e7eb",
};

const arrowStyle: React.CSSProperties = {
  fontSize: "20px",
  color: "#6366f1",
  fontWeight: "bold",
};

const stateStyle: React.CSSProperties = {
  flex: 1,
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  padding: "24px",
  textAlign: "center",
};

const footerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: "12px",
  padding: "14px 20px",
  borderTop: "1px solid #e5e7eb",
  background: "#f9fafb",
};

const cancelBtnStyle: React.CSSProperties = {
  padding: "9px 20px",
  borderRadius: "8px",
  border: "1px solid #d1d5db",
  background: "#fff",
  color: "#374151",
  fontSize: "14px",
  fontWeight: 500,
  cursor: "pointer",
};

const applyBtnStyle = (disabled: boolean): React.CSSProperties => ({
  padding: "9px 22px",
  borderRadius: "8px",
  border: "none",
  background: disabled
    ? "#c4b5fd"
    : "linear-gradient(135deg, #6366f1, #8b5cf6)",
  color: "#fff",
  fontSize: "14px",
  fontWeight: 600,
  cursor: disabled ? "not-allowed" : "pointer",
  boxShadow: disabled ? "none" : "0 2px 8px rgba(99,102,241,0.4)",
});

const Spinner = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="32"
    height="32"
    viewBox="0 0 24 24"
    fill="none"
    stroke="#6366f1"
    strokeWidth="2.5"
    strokeLinecap="round"
    style={{ animation: "spin 0.8s linear infinite" }}
  >
    <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    <path d="M12 2a10 10 0 0 1 10 10" />
  </svg>
);
