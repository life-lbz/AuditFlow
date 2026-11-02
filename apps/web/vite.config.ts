import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// 前端开发服务器；后端地址通过 VITE_API_BASE 注入，默认 http://localhost:8000
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '0.0.0.0',
  },
  preview: {
    port: 5173,
    host: '0.0.0.0',
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
});
