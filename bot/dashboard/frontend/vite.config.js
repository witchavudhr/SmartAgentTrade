import { svelte } from '@sveltejs/vite-plugin-svelte';

export default {
  plugins: [svelte()],
  build: {
    outDir: 'build',
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws':  { target: 'ws://localhost:8000', ws: true },
    },
  },
};
