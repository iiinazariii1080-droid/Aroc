import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  root: '.',
  base: '/arm3d_v2/',

  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
      '@config': resolve(__dirname, 'src/config'),
      '@types': resolve(__dirname, 'src/types'),
      '@domain': resolve(__dirname, 'src/domain'),
      '@data': resolve(__dirname, 'src/data'),
      '@rendering': resolve(__dirname, 'src/rendering'),
      '@ui': resolve(__dirname, 'src/ui'),
    },
  },

  build: {
    outDir: 'dist',
    sourcemap: true,
    assetsDir: 'assets',
    target: 'es2020',
    minify: 'esbuild',
    rollupOptions: {
      input: resolve(__dirname, 'index.html'),
      output: {
        manualChunks: {
          'three-core': ['three'],
        },
        // Deterministic chunk filenames for caching
        chunkFileNames: 'assets/[name]-[hash].js',
        entryFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash].[ext]',
      },
    },
    // Warn on large chunks (Pi has limited bandwidth)
    chunkSizeWarningLimit: 600,
  },

  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8401',
        changeOrigin: true,
      },
      '/static/stl': {
        target: 'http://localhost:8401',
        changeOrigin: true,
      },
    },
  },
});
