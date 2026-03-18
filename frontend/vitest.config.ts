import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    globals: true,
    testTimeout: 60000,
    // Use threads instead of forks to avoid "Timeout waiting for worker to respond" on Windows
    pool: 'threads',
    coverage: {
      provider: 'v8',
      include: [
        'src/lib/**/*.{ts,tsx}',
        'src/components/AgentCard.tsx',
        'src/components/Navbar.tsx',
        'src/components/CostCalculator.tsx',
      ],
      excludeAfterRemap: true,
      exclude: [
        'tests/**',
        '**/*.test.{ts,tsx}',
        '**/*.d.ts',
        '**/main.tsx',
        '**/vite-env.d.ts',
        // Types-only module (TS erased at runtime).
        '**/types.ts',
      ],
      reporter: ['text', 'text-summary'],
      thresholds: {
        lines: 80,
        functions: 80,
        branches: 80,
        statements: 80,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
